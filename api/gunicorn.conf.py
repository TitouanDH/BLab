"""
How gunicorn serves the API, in both stacks (`gunicorn -c gunicorn.conf.py`, from the Dockerfile
and docker-compose.yml). Requests do device work (#63): a Release tears each Link down over the
backbone REST API, a Reservation sets the banner and the Switch accounts over SSH, connecting two
Ports builds a Service. So several requests run at once, and none is cut short halfway.

Nothing in the API needs a single process: what is shared between requests is in the database
(the SVLAN and teardown locks are Postgres advisory locks, which already work across production
and pre-prod), and the in-process caches (backbone session cookies, the admin password each Switch
last took) only save a login when they miss.
"""

bind = '0.0.0.0:8000'
# 3 processes of 4 threads (gunicorn's gthread worker): 12 requests at once, mostly waiting on devices
workers = 3
threads = 4
# With threads, this is how long a worker process may stop answering gunicorn's heartbeat, not a
# limit on a request: a slow request runs to the end, so a Connect that fails still undoes what it
# built. What the user waits for at most is nginx's proxy_read_timeout (frontend/nginx/nginx.conf).
#
# The slowest real request is a Release: a Snapshot read over SSH, then about 9 to 16 backbone calls
# per Link. Each call takes under a second, but up to 17 s when a backbone doesn't answer (3 tries of
# 5 s, backbone.https_cli), 34 s with its login: about 10 s per Link normally, 35 to 70 s per Link
# behind a dead backbone. nginx's 310 s covers a Switch with 25 Links, or 4 behind a dead backbone.
timeout = 300
# On a redeploy, requests under way may finish for this long. Meanwhile the old container no longer
# takes requests (nginx answers 502), so it stays shorter than a Release can take behind a dead backbone.
graceful_timeout = 120
