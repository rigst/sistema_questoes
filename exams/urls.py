from django.urls import path

from . import views

app_name = 'exams'

urlpatterns = [
    path('criar-inline/', views.prova_criar_inline, name='prova_criar_inline'),
    path('<int:pk>/renomear-inline/', views.prova_renomear_inline, name='prova_renomear_inline'),
    path('<int:pk>/excluir-inline/', views.prova_excluir_inline, name='prova_excluir_inline'),
    path('disciplina/criar-inline/', views.disciplina_criar_inline, name='disciplina_criar_inline'),
    path('disciplina/<int:pk>/renomear-inline/', views.disciplina_renomear_inline, name='disciplina_renomear_inline'),
    path('disciplina/<int:pk>/excluir-inline/', views.disciplina_excluir_inline, name='disciplina_excluir_inline'),
]
