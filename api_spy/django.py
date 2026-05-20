import time
from api_spy.core import spy_state, start_spy

class ApiSpyDjangoMiddleware:
    """WSGI-compliant Django middleware that tracks performance telemetry of incoming requests."""
    def __init__(self, get_response):
        self.get_response = get_response
        # Proactively start intercepting stdout/stderr
        start_spy()

    def __call__(self, request):
        spy_state.record_request_start()
        start_time = time.perf_counter()
        
        status = 200
        try:
            response = self.get_response(request)
            status = response.status_code
        except Exception as e:
            status = 500
            raise e
        finally:
            latency_ms = max(0.0, (time.perf_counter() - start_time) * 1000.0)
            
            # Retrieve path pattern using Django resolver_match if available
            path = request.path
            resolver_match = getattr(request, "resolver_match", None)
            if resolver_match and hasattr(resolver_match, "route"):
                path = resolver_match.route
                if not path.startswith("/"):
                    path = "/" + path
            elif status == 404:
                path = "404"
                
            method = request.method
            spy_state.record_request_end(method, path, status, latency_ms)
            
        return response
