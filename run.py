"""
EventSphere - Application Entry Point
Run this script to start the server:
    python run.py
"""
import os
import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    print(f"[*] Starting EventSphere Platform on http://{host}:{port}")
    print(f"[*] OpenAPI Documentation: http://{host}:{port}/docs")
    print("[*] Multi-Agent System: Active")
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips="*"
    )
