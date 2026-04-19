from flask import Flask
import os

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def hello():
    target = os.environ.get('TARGET', 'World')
    return f'Hello {target}! This is my Knative serverless function.\n'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
