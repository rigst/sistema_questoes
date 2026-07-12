from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

User = get_user_model()


class CadastroForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'first_name', 'email')
        labels = {
            'username': 'Usuário',
            'first_name': 'Nome (opcional)',
            'email': 'E-mail',
        }
        help_texts = {
            'email': 'Usado para recuperar a senha.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].required = True
