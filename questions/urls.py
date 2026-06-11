from django.urls import path

from . import views

app_name = 'questions'

urlpatterns = [
    path('disciplina/<int:pk>/', views.disciplina, name='disciplina'),
    path('disciplina/<int:pk>/upload/', views.upload, name='upload'),
    path('importacao/<int:pk>/status/', views.importacao_status, name='importacao_status'),
    path('disciplina/<int:pk>/ia-status/', views.ia_status, name='ia_status'),
    path('<int:pk>/', views.questao_detalhe, name='questao_detalhe'),
    path('<int:pk>/editar/', views.questao_editar, name='questao_editar'),
    path('<int:pk>/confirmar/', views.questao_confirmar, name='questao_confirmar'),
    path('revisar-lote/', views.revisar_lote, name='revisar_lote'),
    path('<int:pk>/excluir/', views.questao_excluir, name='questao_excluir'),
]
