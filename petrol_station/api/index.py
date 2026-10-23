"""Vercel function entrypoint for the Flask application."""
from urllib.parse import parse_qsl, urlencode

from backend.app import create_app


class StripVercelFunctionPrefix:
    def __init__(self, application, prefix):
        self.application = application
        self.prefix = prefix

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == self.prefix:
            query = parse_qsl(environ.get("QUERY_STRING", ""), keep_blank_values=True)
            requested_path = next(
                (value for key, value in query if key == "__vercel_path"),
                "/",
            )
            environ["PATH_INFO"] = requested_path
            environ["QUERY_STRING"] = urlencode(
                [(key, value) for key, value in query if key != "__vercel_path"]
            )
        elif path.startswith(self.prefix + "/"):
            environ["PATH_INFO"] = path[len(self.prefix):]
        return self.application(environ, start_response)


app = create_app()
app.wsgi_app = StripVercelFunctionPrefix(app.wsgi_app, "/api/index")
