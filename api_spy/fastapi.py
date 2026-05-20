import time
from api_spy.core import spy_state, start_spy

class ApiSpyMiddleware:
    """ASGI Middleware for FastAPI / Starlette that tracks HTTP requests and performance telemetry."""
    def __init__(self, app):
        self.app = app
        # Automatically enable telemetry console intercepts on setup
        start_spy()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        spy_state.record_request_start()
        start_time = time.perf_counter()
        
        # Status code placeholder to capture from downstream ASGI events
        status_code = [200]
        
        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_code[0] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            status_code[0] = 500
            raise e
        finally:
            latency_ms = max(0.0, (time.perf_counter() - start_time) * 1000.0)
            
            # Resolve the matched route pattern to prevent path pollution
            route = scope.get("route")
            if route and hasattr(route, "path"):
                path = route.path
            else:
                if status_code[0] == 404:
                    path = "404"
                else:
                    path = scope.get("path", "/")
            
            method = scope.get("method", "GET")
            spy_state.record_request_end(method, path, status_code[0], latency_ms)
