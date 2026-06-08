"""URL configuration — Sistema de Estudos por Questões."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from exams import views as exams_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', exams_views.dashboard, name='dashboard'),
    path('provas/', include('exams.urls')),
    path('questoes/', include('questions.urls')),
    path('prompts/', include('prompts.urls')),
    path('ia/', include('ai.urls')),
    path('relatorios/', include('reports.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
