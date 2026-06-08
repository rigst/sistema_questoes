from django.urls import path

from . import views

app_name = 'prompts'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('novo/', views.form, name='novo'),
    path('<int:pk>/editar/', views.form, name='editar'),
    path('<int:pk>/excluir/', views.excluir, name='excluir'),
]
