"""The shared durable job queue (WS-BRIEF BR-1a). Reference: docs/job_queue.md.

``queue`` holds the SQL: enqueue, claim, hold and settle. ``worker`` holds
the handler registry and the loop. A producer calls :func:`queue.enqueue` in
its own tenant session, and a consumer calls :func:`worker.register`.
"""
