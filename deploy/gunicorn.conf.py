"""Gunicorn configuration — Sistema de Estudos por Questões."""

import multiprocessing

bind = "unix:/home/rodrigostolben/Projetos/sistema_questoes/questoes.sock"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
timeout = 300
keepalive = 2
proc_name = "questoes"
accesslog = "/home/rodrigostolben/Projetos/sistema_questoes/media/gunicorn.access.log"
errorlog = "/home/rodrigostolben/Projetos/sistema_questoes/media/gunicorn.error.log"
loglevel = "info"
daemon = False
