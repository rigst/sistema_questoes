from django.urls import path

from . import views

app_name = 'ai'

urlpatterns = [
    path('aplicar/<int:questao_pk>/', views.aplicar, name='aplicar'),
    path('aplicar-lote/', views.aplicar_lote, name='aplicar_lote'),
    path('resultado/<int:pk>/excluir/', views.resultado_excluir, name='resultado_excluir'),
]
