# -*- coding: utf-8 -*-

###############################################################################
# Bibliotecas utilizadas
###############################################################################
import os
import re
import time
import json
import pyrebase
import xml.etree.ElementTree as ET
from datetime import datetime
from zeep import Client
from flask import (Flask,
                   flash,
                   render_template,
                   request,
                   jsonify,
                   redirect,
                   url_for)
from flask_login import (LoginManager,
                         UserMixin,
                         login_user,
                         logout_user,
                         login_required,
                         current_user)

###############################################################################
# Setup
###############################################################################
# Autor do app
__author__ = 'Daniel Resende'

# Iniciaiza o app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ['SECRET_KEY']

# Conecta ao firebase corrigindo a função sort()
config = {
    'apiKey': os.environ['API_KEY'],
    'authDomain': os.environ['APP_DOMAIN'] + '.firebaseapp.com',
    'databaseURL': 'https://' + os.environ['APP_DOMAIN'] + '.firebaseio.com',
    'storageBucket': os.environ['APP_DOMAIN'] + '.appspot.com'
}

firebase = pyrebase.initialize_app(config)
auth = firebase.auth()
db = firebase.database()

# Configura login/logout manager
login_manager = LoginManager()
login_manager.init_app(app)


class User(UserMixin):
    def __init__(self, localId, token, refresh_token):
        self.id = localId
        self.refreshToken = refresh_token
        self.idToken = token

    def reset_token(self):
        token = auth.refresh(self.refreshToken)['idToken']
        self.idToken = token
        return None

    def start_session(self, time):
        self.startSession = time

    def session_over(self):
        return (time.time() - self.startSession) > 3000

    def is_active(self):
        return True

    def is_authenticated(self):
        return True

    def get_id(self):
        return self.id, self.refreshToken


@login_manager.user_loader
def load_user(user_info):
    user_id, user_refresh = user_info
    token = auth.refresh(user_refresh)['idToken']
    user = User(user_id, token, user_refresh)
    user.start_session(time.time())
    return user


# Conecta aos correios
url_correios = 'http://webservice.correios.com.br/service/rastro/Rastro.wsdl'
try:
    client = Client(url_correios)
except:
    client = None


###############################################################################
# Página inicial/login
###############################################################################
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['user_email']
        password = request.form['user_pass']
        try:
            user = auth.sign_in_with_email_and_password(email, password)
            user = User(
                user['localId'],
                user['idToken'],
                user['refreshToken']
            )
            login_user(user)
            return redirect(url_for('objetos'))
        except Exception as e:
            mensagem = json.loads(e.strerror)['error']['message']
            mensagem = mensagem.replace('_', ' ')
            flash(mensagem)
            return render_template('login.html')
    else:
        return render_template('login.html')


###############################################################################
# Redefinir senha
###############################################################################
@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        email = request.form['user_email']
        auth.send_password_reset_email(email)
        return redirect(url_for('login'))
    else:
        return render_template('reset_password.html')


###############################################################################
# Logout
###############################################################################
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


###############################################################################
# Listagem das encomendas
###############################################################################
@app.route('/objetos', methods=['GET', 'POST'])
@login_required
def objetos():
    if current_user.is_active and current_user.session_over():
        current_user.reset_token()

    if request.method == 'POST':

        data = {
            'nota_fiscal': request.form['notaFiscal'],
            'cod_cliente': request.form['codCliente'],
            'nome_cliente': request.form['nomeCliente'],
            'cidade': request.form['cidade'],
            'uf': request.form['uf'],
            'data_postagem': request.form['dataPostagem'],
            'codigo': request.form['codigo'],
            'ult_atual': request.form['dataPostagem'],
            'status': 'Objeto postado'
        }

        # Acerta formato da data (problema com o Chrome)
        m = re.search('([0-9]+)-([0-9]+)-([0-9]+)',
                      request.form['dataPostagem'])
        if m is not None:
            data['data_postagem'] = m.group(3)
            data['data_postagem'] = data['data_postagem'] + '/'
            data['data_postagem'] = data['data_postagem'] + m.group(2)
            data['data_postagem'] = data['data_postagem'] + '/'
            data['data_postagem'] = data['data_postagem'] + m.group(1)
            data['ult_atual'] = data['data_postagem']

        objeto = db.child('objetos')
        objeto = objeto.child(data['codigo'])
        objeto.set(data, current_user.idToken)

    try:
        context = []
        objetos = db.child('objetos')
        objetos = objetos.get(current_user.idToken)
        for objeto in objetos.each():
            objeto = dict(objeto.val())
            objeto['data_postagem'] = datetime.strptime(
                objeto['data_postagem'],
                '%d/%m/%Y')
            objeto['ult_atual'] = datetime.strptime(
                objeto['ult_atual'],
                '%d/%m/%Y')
            delta = datetime.now() - objeto['ult_atual']
            if delta.days > 120 and \
               objeto['status'] == u'Objeto entregue ao destinatário':
                objeto_entregue = db.child('objetos')
                objeto_entregue = objeto_entregue.child(objeto['codigo'])
                objeto_entregue.remove(current_user.idToken)
            else:
                context.append(objeto)
        context = sorted(context,
                         key=lambda k: k['data_postagem'],
                         reverse=True)
    except:
        context = []

    return render_template('index.html', objetos=context)


###############################################################################
# API para checar o status de um determinado objeto
###############################################################################
@app.route('/objetos/<codigo>', methods=['GET', 'POST'])
@login_required
def altera_objeto(codigo):

    if current_user.is_active and current_user.session_over():
        current_user.reset_token()

    if request.method == 'POST':
        request_data = {
            'usuario': 'ECT',
            'senha': 'SRO',
            'tipo': 'L',
            'resultado': 'T',
            'lingua': '101',
            'objetos': codigo
        }

        status_entrega = {}
        if client is not None:
            with client.options(raw_response=True):
                response = client.service.buscaEventos(**request_data)
                root = ET.fromstring(response.content)
            status_entrega['ult_atual'] = root.find('.//data').text
            status_entrega['status'] = root.find('.//descricao').text

            objeto = db.child("objetos")
            objeto = objeto.child(str(codigo))
            objeto.update(status_entrega, current_user.idToken)

        else:
            objeto = db.child("objetos")
            objeto = objeto.child(str(codigo))
            objeto = objeto.get(current_user.idToken).val()
            status_entrega['ult_atual'] = objeto['ult_atual']
            status_entrega['descricao'] = objeto['status']

        return jsonify(status_entrega)

    else:
        objeto = db.child("objetos")
        objeto = objeto.child(str(codigo))
        objeto.remove(current_user.idToken)
        return redirect(url_for('objetos'))


###############################################################################
# Inicia o servidor
###############################################################################
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
