# py-api-spy

[![PyPI version](https://img.shields.io/pypi/v/py-api-spy.svg)](https://pypi.org/project/py-api-spy/)
[![Python version](https://img.shields.io/pypi/pyversions/py-api-spy.svg)](https://pypi.org/project/py-api-spy/)
[![License](https://img.shields.io/pypi/l/py-api-spy.svg)](https://pypi.org/project/py-api-spy/)

A zero-dependency, lightweight terminal performance dashboard for Python web frameworks.

This is the Python sibling of the `@chabdulwahab/api-spy` Node.js library. It maintains the identical layout, telemetry keys, and minimalist design philosophy.

## Features

- **Zero Dependencies**: Implemented using only standard Python library modules.
- **Low Overhead**: Tailored metrics tracking with minimal CPU and memory impact.
- **Sticky Terminal Dashboard**: Persisted at the bottom of the stdout stream while application logs print normally above it.
- **Thread Safe**: Concurrent requests are serialized using reentrant locking mechanisms.
- **Framework Support**: Built-in middlewares for FastAPI / Starlette (ASGI) and Django / DRF (WSGI).

## Installation

Install the package via pip:

```bash
pip install py-api-spy
```

## Usage

### FastAPI / Starlette

Add the ASGI middleware to your FastAPI application:

```python
from fastapi import FastAPI
from api_spy import ApiSpyMiddleware

app = FastAPI()
app.add_middleware(ApiSpyMiddleware)

@app.get("/")
def read_root():
    return {"hello": "world"}
```

### Django / DRF

Add the WSGI middleware to your Django `MIDDLEWARE` list in `settings.py`:

```python
MIDDLEWARE = [
    # ... other middlewares ...
    "api_spy.ApiSpyDjangoMiddleware",
]
```

## Dashboard Design

The dashboard takes exactly 10 lines and maintains a hardcoded width of 72 characters:

- **Latency States**: Green `[✓]` for fast routes (<= 200ms) and red `[!]` for slow routes (> 200ms).
- **Dot-leaders**: Dim gray dots linking paths with their corresponding metrics.
- **Cross-Platform Memory**: Retrieves resident set size (RAM) for Linux, macOS, and Windows.

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Active: 1          │ Total: 15          │ RAM: 14.5 MB               │
├──────────────────────────────────────────────────────────────────────┤
│ Slowest Routes (Top 5)                                               │
│ [✓] GET     /.............................................. 5ms (x8) │
│ [!] POST    /api/v1/users.............................. 205ms (x2)   │
│ -                                                                    │
│ -                                                                    │
│ -                                                                    │
└──────────────────────────────────────────────────────────────────────┘
```

## License

MIT
