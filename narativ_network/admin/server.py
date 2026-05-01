"""Admin web UI + JSON API.

URLs:
  GET  /                      dashboard (now/next + run-log tail)
  GET  /shows                 inventory (shows + episode counts, statuses)
  GET  /schedule              week grid
  POST /api/slot              create/update a slot   (token-gated)
  POST /api/slot/delete       delete a slot          (token-gated)
  POST /api/regenerate        force playlist regen   (token-gated)
  POST /api/process_now       run processor pass     (token-gated)
  POST /api/poll_now          run ingest pass        (token-gated)
  GET  /api/now_playing       JSON: current + next 10
  GET  /healthz               cheap health probe

Auth: Bearer token from cfg.admin.admin_token. Read endpoints are open
on localhost; state changers require the token.

The HTML pages use minimal vanilla JS + a tiny drag-drop helper for the
schedule. No build step.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Config, absolute_path, load_config
from ..db import connect

THIS_DIR = Path(__file__).parent
TEMPLATES = Jinja2Templates(directory=str(THIS_DIR / "web" / "templates"))
STATIC_DIR = THIS_DIR / "web" / "static"


def create_app(cfg: Config | None = None) -> FastAPI:
    cfg = cfg or load_config()
    app = FastAPI(title="Narativ Network")
    app.state.cfg = cfg
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def require_token(request: Request) -> None:
        if not cfg.admin.admin_token:
            raise HTTPException(500, "admin_token not configured")
        header = request.headers.get("Authorization", "")
        if header != f"Bearer {cfg.admin.admin_token}":
            raise HTTPException(401, "missing or wrong admin token")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        conn = connect(cfg)
        now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
        upcoming = conn.execute(
            """SELECT p.scheduled_at, p.rule_used, p.fallback_level, p.status,
                      p.duration_sec, e.title AS ep_title, s.title AS show_title
               FROM playlist p
               LEFT JOIN episodes e ON e.id = p.episode_id
               LEFT JOIN shows s ON s.id = e.show_id
               WHERE p.scheduled_at >= ?
               ORDER BY p.scheduled_at ASC LIMIT 12""",
            (now_utc,),
        ).fetchall()
        recent = conn.execute(
            """SELECT started_at, status, detail FROM run_log
               ORDER BY started_at DESC LIMIT 25"""
        ).fetchall()
        conn.close()
        return TEMPLATES.TemplateResponse(request=request, name="dashboard.html",
                                           context={"upcoming": upcoming, "recent": recent})

    @app.get("/shows", response_class=HTMLResponse)
    def shows(request: Request):
        conn = connect(cfg)
        rows = conn.execute(
            """SELECT shows.*,
                      (SELECT COUNT(*) FROM episodes e WHERE e.show_id=shows.id AND e.status='ready') AS ready_count,
                      (SELECT COUNT(*) FROM episodes e WHERE e.show_id=shows.id AND e.status='descript_queued') AS descript_count,
                      (SELECT COUNT(*) FROM episodes e WHERE e.show_id=shows.id AND e.status='failed') AS failed_count
               FROM shows ORDER BY title"""
        ).fetchall()
        conn.close()
        return TEMPLATES.TemplateResponse(request=request, name="shows.html",
                                           context={"shows": rows})

    @app.get("/schedule", response_class=HTMLResponse)
    def schedule(request: Request):
        conn = connect(cfg)
        slots = [dict(r) for r in conn.execute(
            "SELECT * FROM slots WHERE enabled=1 ORDER BY day_of_week, start_minute").fetchall()]
        shows_rows = [dict(r) for r in conn.execute(
            "SELECT id, title FROM shows ORDER BY title").fetchall()]
        conn.close()
        return TEMPLATES.TemplateResponse(request=request, name="schedule.html",
                                           context={"slots": slots, "shows": shows_rows,
                                                    "slot_minutes": cfg.schedule.slot_minutes})

    @app.get("/api/now_playing")
    def api_now_playing():
        conn = connect(cfg)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = conn.execute(
            """SELECT p.scheduled_at, p.rule_used, p.fallback_level,
                      p.duration_sec, p.status,
                      e.title AS ep_title, s.title AS show_title
               FROM playlist p
               LEFT JOIN episodes e ON e.id=p.episode_id
               LEFT JOIN shows s ON s.id=e.show_id
               WHERE p.scheduled_at >= ?
               ORDER BY p.scheduled_at ASC LIMIT 11""",
            (now,),
        ).fetchall()
        conn.close()
        return JSONResponse({
            "current": dict(rows[0]) if rows else None,
            "next": [dict(r) for r in rows[1:]],
        })

    @app.post("/api/slot", dependencies=[Depends(require_token)])
    def api_slot_upsert(
        slot_id: Optional[int] = Form(None),
        label: str = Form(""),
        day_of_week: int = Form(...),
        start_minute: int = Form(...),
        length_min: int = Form(...),
        rule_type: str = Form(...),
        rule_payload: str = Form("{}"),
        recurrence: str = Form("weekly"),
    ):
        try:
            json.loads(rule_payload)
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"rule_payload is not valid JSON: {e}")

        conn = connect(cfg)
        if slot_id:
            conn.execute(
                """UPDATE slots SET label=?, day_of_week=?, start_minute=?,
                       length_min=?, rule_type=?, rule_payload=?, recurrence=?
                   WHERE id=?""",
                (label, day_of_week, start_minute, length_min, rule_type,
                 rule_payload, recurrence, slot_id),
            )
        else:
            conn.execute(
                """INSERT INTO slots
                   (label, day_of_week, start_minute, length_min,
                    rule_type, rule_payload, recurrence)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (label, day_of_week, start_minute, length_min, rule_type,
                 rule_payload, recurrence),
            )
        conn.close()
        return {"ok": True}

    @app.post("/api/slot/delete", dependencies=[Depends(require_token)])
    def api_slot_delete(slot_id: int = Form(...)):
        conn = connect(cfg)
        conn.execute("DELETE FROM slots WHERE id=?", (slot_id,))
        conn.close()
        return {"ok": True}

    @app.post("/api/regenerate", dependencies=[Depends(require_token)])
    def api_regenerate():
        from ..schedule.playlist import regenerate_rolling_playlist
        return regenerate_rolling_playlist(cfg)

    @app.post("/api/process_now", dependencies=[Depends(require_token)])
    def api_process_now():
        from ..process import process_all_pending
        return process_all_pending(cfg)

    @app.post("/api/poll_now", dependencies=[Depends(require_token)])
    def api_poll_now():
        from ..ingest.poller import poll_once
        return poll_once(cfg)

    # ── live break-in / return / standby (OBS scene control) ─────────
    @app.post("/api/break_in", dependencies=[Depends(require_token)])
    def api_break_in(reason: str = Form("")):
        from ..obs import break_in
        try:
            return break_in(cfg, reason=reason)
        except Exception as e:
            raise HTTPException(503, f"OBS unreachable: {e}")

    @app.post("/api/return_to_air", dependencies=[Depends(require_token)])
    def api_return_to_air():
        from ..obs import return_to_air
        try:
            return return_to_air(cfg)
        except Exception as e:
            raise HTTPException(503, f"OBS unreachable: {e}")

    @app.post("/api/standby", dependencies=[Depends(require_token)])
    def api_standby(reason: str = Form("")):
        from ..obs import go_standby
        try:
            return go_standby(cfg, reason=reason)
        except Exception as e:
            raise HTTPException(503, f"OBS unreachable: {e}")

    @app.get("/api/obs_status")
    def api_obs_status():
        from ..obs import OBSController
        try:
            ctl = OBSController(cfg)
            return {"current_scene": ctl.current(), "stream": ctl.stream_status()}
        except Exception as e:
            return {"current_scene": None, "error": str(e)}

    # ── Live cue: list / arm / GO NOW ────────────────────────────────
    @app.get("/api/live_upcoming")
    def api_live_upcoming():
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn = connect(cfg)
        rows = conn.execute(
            """SELECT ls.*, shows.title AS show_title, shows.slug AS show_slug
               FROM live_sessions ls
               JOIN shows ON shows.id = ls.show_id
               WHERE ls.status IN ('pending','armed','live')
                  OR ls.scheduled_at >= datetime(?, '-30 minutes')
               ORDER BY ls.scheduled_at ASC LIMIT 50""",
            (now,),
        ).fetchall()
        conn.close()
        return JSONResponse({"sessions": [dict(r) for r in rows]})

    @app.post("/api/live_arm", dependencies=[Depends(require_token)])
    def api_live_arm(session_id: int = Form(...), source_url: str = Form(...),
                     armed_by: str = Form("")):
        if not source_url.strip():
            raise HTTPException(400, "source_url required")
        conn = connect(cfg)
        conn.execute(
            """UPDATE live_sessions
               SET source_url=?, status='armed', armed_at=?, armed_by=?, note=NULL
               WHERE id=? AND status IN ('pending','armed')""",
            (source_url.strip(),
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             armed_by or "admin", session_id),
        )
        conn.close()
        return {"ok": True}

    @app.post("/api/live_go_now", dependencies=[Depends(require_token)])
    def api_live_go_now(session_id: int = Form(...)):
        """Force-cut: even if before scheduled_at, switch OBS to LIVE
        with this session's URL right now. Useful when a contributor
        is late and the producer has the URL.
        """
        from ..obs import OBSController
        conn = connect(cfg)
        s = conn.execute("SELECT * FROM live_sessions WHERE id=?",
                         (session_id,)).fetchone()
        if not s:
            conn.close(); raise HTTPException(404, "no such session")
        if not s["source_url"]:
            conn.close(); raise HTTPException(400, "session has no source_url; arm it first")
        try:
            ctl = OBSController(cfg)
            ctl.set_live_source_url(s["source_url"])
            ctl.to_live()
        except Exception as e:
            conn.close(); raise HTTPException(503, f"OBS unreachable: {e}")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "UPDATE live_sessions SET status='live', started_at=?, note='manual GO NOW' WHERE id=?",
            (now, session_id),
        )
        conn.close()
        return {"ok": True, "from": s["status"]}

    @app.post("/api/live_skip", dependencies=[Depends(require_token)])
    def api_live_skip(session_id: int = Form(...), reason: str = Form("")):
        conn = connect(cfg)
        conn.execute(
            "UPDATE live_sessions SET status='skipped', note=? WHERE id=?",
            (reason or "skipped by admin", session_id),
        )
        conn.close()
        return {"ok": True}

    # ── nginx-rtmp on_publish hook ───────────────────────────────────
    # nginx-rtmp calls this URL when a client tries to publish to the
    # /internal/live application. We validate the publish "name" (which
    # the contributor configures as their stream key) against
    # `shows.live_stream_key`. HTTP 2xx allows; non-2xx rejects.
    @app.post("/api/rtmp_authorize")
    async def api_rtmp_authorize(request: Request):
        form = await request.form()
        name = (form.get("name") or "").strip()           # the stream key
        app_name = (form.get("app") or "").strip()        # nginx app name
        client_ip = (form.get("addr") or "").strip()
        if not name:
            raise HTTPException(403, "missing stream key")
        conn = connect(cfg)
        row = conn.execute(
            "SELECT id, slug, title FROM shows "
            "WHERE live_capable=1 AND live_source_kind='rtmp_push' "
            "AND live_stream_key=?",
            (name,),
        ).fetchone()
        conn.close()
        if not row:
            raise HTTPException(403, "unknown stream key")
        # Stamp run_log so the dashboard sees who's connected.
        try:
            conn = connect(cfg)
            conn.execute(
                "INSERT INTO run_log (started_at, status, detail) VALUES (?,?,?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "rtmp_publish_accepted",
                 f"app={app_name} show={row['slug']} ip={client_ip}"),
            )
            conn.close()
        except Exception:
            pass
        return {"ok": True, "show_id": row["id"], "show_slug": row["slug"]}

    @app.get("/live", response_class=HTMLResponse)
    def live_cue_page(request: Request):
        conn = connect(cfg)
        sessions = conn.execute(
            """SELECT ls.*, shows.title AS show_title, shows.slug AS show_slug,
                      shows.live_source_kind AS show_source_kind,
                      shows.live_stream_key  AS show_stream_key
               FROM live_sessions ls
               JOIN shows ON shows.id = ls.show_id
               WHERE ls.status IN ('pending','armed','live')
               ORDER BY ls.scheduled_at ASC LIMIT 50"""
        ).fetchall()
        live_shows = conn.execute(
            "SELECT id, slug, title, live_source_kind, live_stream_key, live_default_url "
            "FROM shows WHERE live_capable=1 ORDER BY title"
        ).fetchall()
        conn.close()
        return TEMPLATES.TemplateResponse(request=request, name="live_cue.html",
                                           context={"sessions": sessions, "shows": live_shows})

    # ── Transcripts: search + per-episode read ──────────────────────
    @app.get("/api/search")
    def api_search(q: str = "", limit: int = 25, show_id: int | None = None):
        from ..transcripts import search
        return {"query": q, "results": search(cfg, q, limit=limit, show_id=show_id)}

    @app.get("/api/transcript/{episode_id}")
    def api_transcript(episode_id: int):
        conn = connect(cfg)
        row = conn.execute(
            """SELECT t.*, episodes.title AS ep_title, shows.title AS show_title
               FROM transcripts t
               JOIN episodes ON episodes.id = t.episode_id
               LEFT JOIN shows ON shows.id = episodes.show_id
               WHERE t.episode_id=?""",
            (episode_id,),
        ).fetchone()
        conn.close()
        if not row:
            raise HTTPException(404, "no transcript for that episode")
        return dict(row)

    @app.post("/api/transcribe_now", dependencies=[Depends(require_token)])
    def api_transcribe_now(episode_id: int = Form(...), force: bool = Form(False)):
        from ..transcripts import transcribe_one
        return transcribe_one(cfg, episode_id, force=force)

    @app.get("/search", response_class=HTMLResponse)
    def search_page(request: Request, q: str = ""):
        results = []
        if q:
            from ..transcripts import search
            results = search(cfg, q, limit=50)
        return TEMPLATES.TemplateResponse(request=request, name="search.html",
                                           context={"q": q, "results": results})

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app
