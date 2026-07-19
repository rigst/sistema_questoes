"""URL configuration — Sistema de Estudos por Questões."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import TemplateView

from exams import views as exams_views

urlpatterns = [
    path('admin/', admin.site.urls),
    # Páginas legais (LGPD): acessíveis sem login.
    path('privacidade/', TemplateView.as_view(template_name='legal/privacidade.html'), name='privacidade'),
    path('termos/', TemplateView.as_view(template_name='legal/termos.html'), name='termos'),
    path('accounts/', include('accounts.urls')),
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('senha/', auth_views.PasswordResetView.as_view(), name='password_reset'),
    path('senha/enviado/', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('senha/redefinir/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('senha/concluido/', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
    path('', exams_views.dashboard, name='dashboard'),
    path('provas/', include('exams.urls')),
    path('questoes/', include('questions.urls')),
    path('prompts/', include('prompts.urls')),
    path('ia/', include('ai.urls')),
    path('relatorios/', include('reports.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
