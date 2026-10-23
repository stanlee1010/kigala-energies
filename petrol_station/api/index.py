"""Vercel function entrypoint for the Flask application."""
from backend.app import create_app


class StripVercelFunctionPrefix:
    def __init__(self, application, prefix):
        self.application = application
        self.prefix = prefix

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == self.prefix:
            environ["PATH_INFO"] = "/"
        elif path.startswith(self.prefix + "/"):
            environ["PATH_INFO"] = path[len(self.prefix):]
        return self.application(environ, start_response)


app = create_app()
app.wsgi_app = StripVercelFunctionPrefix(app.wsgi_app, "/api/index")
