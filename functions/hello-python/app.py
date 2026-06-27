import os
from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    target = os.environ.get('TARGET', 'World')
    return f"Hello, {target}! From a Python function on Knative.\n"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
