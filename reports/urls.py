from django.urls import path

from . import views

app_name = 'reports'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('gerar/', views.gerar, name='gerar'),
    path('<int:pk>/excluir/', views.excluir, name='excluir'),
]
