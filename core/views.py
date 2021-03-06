# -*- coding: utf-8 -*-
from django import template
from django import forms
from django.http import HttpResponse, Http404
from django.shortcuts import render, render_to_response
from django.template import Context, loader
from django.views.generic import View, TemplateView, ListView, DetailView
from django.db.models import Q
from django.core.cache import caches
from django.core.exceptions import PermissionDenied
from django.contrib import auth
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from core.models import Article, Category, Carousel, Column, Nav, News
from sec_comments.models import Comment
from sec_auth.models import SecUser
from sec_system.models import Link
from sec_auth.forms import SecUserCreationForm, SecPasswordRestForm
from django.conf import settings
import datetime
import time
import json
import logging

# Caching
try:
    cache = caches['memcache']
except ImportError as e:
    cache = caches['default']

# logger
logger = logging.getLogger(__name__)


class BaseMixin(object):
    def get_context_data(self, *args, **kwargs):
        context = super(BaseMixin, self).get_context_data(**kwargs)
        try:
            # Website title, etc.
            context['website_title'] = settings.WEBSITE_TITLE
            context['website_welcome'] = settings.WEBSITE_WELCOME
            # popular articles
            context['hot_article_list'] = \
                Article.objects.order_by("-view_times")[0:10]
            # Navigation bar
            context['nav_list'] = Nav.objects.filter(status=0)
            # latest comment
            context['latest_comment_list'] = \
                Comment.objects.order_by("-create_time")[0:10]
            # Links
            context['links'] = Link.objects.order_by('create_time').all()
            colors = ['primary', 'success', 'info', 'warning', 'danger']
            for index, link in enumerate(context['links']):
                link.color = colors[index % len(colors)]
            # Users unread messages
            user = self.request.user
            if user.is_authenticated():
                context['notification_count'] = \
                    user.to_user_notification_set.filter(is_read=0).count()
        except Exception as e:
            logger.error(u'[BaseMixin]Error loading basic information')

        return context


class IndexView(BaseMixin, ListView):
    template_name = 'blog/index.html'
    context_object_name = 'article_list'
    paginate_by = settings.PAGE_NUM  # Paging - page number

    def get_context_data(self, **kwargs):
        # Carousel
        kwargs['carousel_page_list'] = Carousel.objects.all()
        return super(IndexView, self).get_context_data(**kwargs)

    def get_queryset(self):
        article_list = Article.objects.filter(status=0)
        return article_list


class ArticleView(BaseMixin, DetailView):
    queryset = Article.objects.filter(Q(status=0) | Q(status=1))
    template_name = 'blog/article.html'
    context_object_name = 'article'
    slug_field = 'en_title'

    def get(self, request, *args, **kwargs):
        # Visits statistics access article
        if 'HTTP_X_FORWARDED_FOR' in request.META:
            ip = request.META['HTTP_X_FORWARDED_FOR']
        else:
            ip = request.META['REMOTE_ADDR']
        self.cur_user_ip = ip

        en_title = self.kwargs.get('slug')
        # Get 15 * 60 s within the time of this article visited all ip
        visited_ips = cache.get(en_title, [])

        # If there is no ip put the article views +1
        if ip not in visited_ips:
            try:
                article = self.queryset.get(en_title=en_title)
            except Article.DoesNotExist:
                logger.error(u'[ArticleView]Access a nonexistent article:[%s]' % en_title)
                raise Http404
            else:
                article.view_times += 1
                article.save()
                visited_ips.append(ip)

            # refresh cache
            cache.set(en_title, visited_ips, 15*60)

        return super(ArticleView, self).get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        # comment
        en_title = self.kwargs.get('slug', '')
        kwargs['comment_list'] = \
            self.queryset.get(en_title=en_title).comment_set.all()
        return super(ArticleView, self).get_context_data(**kwargs)


class AllView(BaseMixin, ListView):
    template_name = 'blog/all.html'
    context_object_name = 'article_list'

    def get_context_data(self, **kwargs):
        kwargs['category_list'] = Category.objects.all()
        kwargs['PAGE_NUM'] = settings.PAGE_NUM
        return super(AllView, self).get_context_data(**kwargs)

    def get_queryset(self):
        article_list = Article.objects.filter(
            status=0
        ).order_by("-pub_time")[0:settings.PAGE_NUM]
        return article_list

    def post(self, request, *args, **kwargs):
        val = self.request.POST.get("val", "")
        sort = self.request.POST.get("sort", "time")
        start = self.request.POST.get("start", 0)
        end = self.request.POST.get("end", settings.PAGE_NUM)

        start = int(start)
        end = int(end)

        if sort == "time":
            sort = "-pub_time"
        elif sort == "recommend":
            sort = "-view_times"
        else:
            sort = "-pub_time"

        if val == "all":
            article_list = \
                Article.objects.filter(status=0).order_by(sort)[start:end+1]
        else:
            try:
                article_list = Category.objects.get(
                                   name=val
                               ).article_set.filter(
                                   status=0
                               ).order_by(sort)[start:end+1]
            except Category.DoesNotExist:
                logger.error(u'[AllView]This category does not exist:[%s]' % val)
                raise PermissionDenied

        isend = len(article_list) != (end-start+1)

        article_list = article_list[0:end-start]

        html = ""
        for article in article_list:
            html += template.loader.get_template(
                        'blog/include/all_post.html'
                    ).render(template.Context({'post': article}))

        mydict = {"html": html, "isend": isend}
        return HttpResponse(
            json.dumps(mydict),
            content_type="application/json"
        )


class SearchView(BaseMixin, ListView):
    template_name = 'blog/search.html'
    context_object_name = 'article_list'
    paginate_by = settings.PAGE_NUM

    def get_context_data(self, **kwargs):
        kwargs['s'] = self.request.GET.get('s', '')
        return super(SearchView, self).get_context_data(**kwargs)

    def get_queryset(self):
        # Get the search keywords
        s = self.request.GET.get('s', '')
        #Search keyword in the title of the article, summary and tags in
        article_list = Article.objects.only(
            'title', 'summary', 'tags'
        ).filter(
            Q(title__icontains=s) |
            Q(summary__icontains=s) |
            Q(tags__icontains=s),
            status=0
        )
        return article_list


class TagView(BaseMixin, ListView):
    template_name = 'blog/tag.html'
    context_object_name = 'article_list'
    paginate_by = settings.PAGE_NUM

    def get_queryset(self):
        tag = self.kwargs.get('tag', '')
        article_list = \
            Article.objects.only('tags').filter(tags__icontains=tag, status=0)

        return article_list


class CategoryView(BaseMixin, ListView):
    template_name = 'blog/category.html'
    context_object_name = 'article_list'
    paginate_by = settings.PAGE_NUM

    def get_queryset(self):
        category = self.kwargs.get('category', '')
        try:
            article_list = \
                Category.objects.get(name=category).article_set.all()
        except Category.DoesNotExist:
            logger.error(u'[CategoryView]This category does not exist:[%s]' % category)
            raise Http404

        return article_list


class UserView(BaseMixin, TemplateView):
    template_name = 'blog/user.html'

    def get(self, request, *args, **kwargs):

        if not request.user.is_authenticated():
            logger.error(u'[UserView]The user does not log in')
            return render(request, 'blog/login.html')

        slug = self.kwargs.get('slug')

        if slug == 'changetx':
            self.template_name = 'blog/user_changetx.html'
        elif slug == 'changepassword':
            self.template_name = 'blog/user_changepassword.html'
        elif slug == 'changeinfo':
            self.template_name = 'blog/user_changeinfo.html'
        elif slug == 'message':
            self.template_name = 'blog/user_message.html'
        elif slug == 'notification':
            self.template_name = 'blog/user_notification.html'

        return super(UserView, self).get(request, *args, **kwargs)

        logger.error(u'[UserView]This interface does not exist')
        raise Http404

    def get_context_data(self, **kwargs):
        context = super(UserView, self).get_context_data(**kwargs)

        slug = self.kwargs.get('slug')

        if slug == 'notification':
            context['notifications'] = \
                self.request.user.to_user_notification_set.order_by(
                    '-create_time'
                ).all()

        return context


class ColumnView(BaseMixin, ListView):
    queryset = Column.objects.all()
    template_name = 'blog/column.html'
    context_object_name = 'article_list'
    paginate_by = settings.PAGE_NUM

    def get_context_data(self, **kwargs):
        column = self.kwargs.get('column', '')
        try:
            kwargs['column'] = Column.objects.get(name=column)
        except Column.DoesNotExist:
            logger.error(u'[ColumnView]Access column does not exist: [%s]' % column)
            raise Http404

        return super(ColumnView, self).get_context_data(**kwargs)

    def get_queryset(self):
        column = self.kwargs.get('column', '')
        try:
            article_list = Column.objects.get(name=column).article.all()
        except Column.DoesNotExist:
            logger.error(u'[ColumnView]Access column does not exist: [%s]' % column)
            raise Http404

        return article_list


class NewsView(BaseMixin, TemplateView):
    template_name = 'blog/news.html'

    def get_context_data(self, **kwargs):
        timeblocks = []

        # Get the date and termination
        start_day = self.request.GET.get("start", "0")
        end_day = self.request.GET.get("end", "6")
        start_day = int(start_day)
        end_day = int(end_day)

        start_date = datetime.datetime.now()

        # Get the url time off information
        for x in range(start_day, end_day+1):
            date = start_date - datetime.timedelta(x)
            news_list = News.objects.filter(
                pub_time__year=date.year,
                pub_time__month=date.month,
                pub_time__day=date.day
            )

            if news_list:
                timeblocks.append(news_list)

        kwargs['timeblocks'] = timeblocks
        kwargs['active'] = start_day/7  # li in the active display

        return super(NewsView, self).get_context_data(**kwargs)
