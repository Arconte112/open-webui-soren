#!/usr/bin/env python3
"""
Worker simple que ejecuta tareas programadas contra Soren y guarda un preview.

Ejecución sugerida:
    PYTHONPATH=backend python scheduled_tasks_worker.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
import calendar
from typing import Any, Optional
import urllib.error
import urllib.request
import argparse

# Asegura que podamos importar open_webui.*
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Garantiza WEBUI_SECRET_KEY antes de importar módulos que lo requieren.
if not os.environ.get("WEBUI_SECRET_KEY"):
    secret_path = os.path.join(BACKEND_DIR, ".webui_secret_key")
    try:
        with open(secret_path, "r", encoding="utf-8") as fh:
            os.environ["WEBUI_SECRET_KEY"] = fh.read().strip()
    except FileNotFoundError:
        # En entornos donde no exista el archivo, deja el valor vacío y permitirá que falle más adelante de forma explícita.
        os.environ.setdefault("WEBUI_SECRET_KEY", "")

from open_webui.scheduled_tasks.repository import (  # noqa: E402
    get_due_tasks,
    update_task_status,
    update_last_error,
    update_last_response_preview,
    reschedule_task,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_COMPLETED,
)

DEFAULT_SLEEP_SECONDS = int(os.getenv("SCHEDULED_TASKS_SLEEP", "60"))
DEFAULT_BASE_URL = os.getenv("SOREN_BASE_URL", "http://localhost:8080")
# Token hardcodeado para compatibilidad; puede sobreescribirse con SOREN_API_TOKEN si se desea.
DEFAULT_API_TOKEN = os.getenv(
    "SOREN_API_TOKEN",
    "sk-7e7b1636fec4427a8e1cfe9a217984a1",
)
N8N_NOTIFY_URL = os.getenv(
    "SCHEDULED_TASKS_NOTIFY_URL",
    "https://n8n.automatadr.com/webhook/notificacion",
)


def call_soren_wait(base_url: str, token: str, prompt: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/soren/call"
    payload = {"prompt": prompt, "wait_for_response": True}

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(request) as response:
        charset = response.headers.get_content_charset("utf-8")
        return json.loads(response.read().decode(charset))


def post_notify(task_id: int, task_prompt: str, run_at: datetime, content: str, chat_id: Optional[str], assistant_message_id: Optional[str]) -> None:
    payload = {
        "task_id": task_id,
        "prompt": task_prompt,
        "run_at": run_at.isoformat(),
        "chat_id": chat_id,
        "assistant_message_id": assistant_message_id,
        "response_content": content,
    }

    request = urllib.request.Request(
        N8N_NOTIFY_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        # Best effort: just consume the response to avoid leaking connections.
        response.read()


def _next_run(current: datetime, recurrence: str) -> Optional[datetime]:
    rec = (recurrence or "").lower()
    if rec == "daily":
        return current + timedelta(days=1)
    if rec == "weekly":
        return current + timedelta(days=7)
    if rec == "monthly":
        year = current.year
        month = current.month + 1
        if month == 13:
            month = 1
            year += 1
        day = min(current.day, calendar.monthrange(year, month)[1])
        return current.replace(year=year, month=month, day=day)
    return None


def run_once(base_url: str, token: str) -> None:
    now = datetime.now(timezone.utc)
    tasks = get_due_tasks(now)
    if not tasks:
        return

    print(f"[scheduled_tasks] {len(tasks)} tareas due a {now.isoformat()}")
    for task in tasks:
        task_id = task.id
        print(f"[scheduled_tasks] Ejecutando tarea {task_id} (run_at={task.run_at.isoformat()}, notify={task.notify})")
        update_task_status(task_id, STATUS_RUNNING)

        try:
            res = call_soren_wait(base_url, token, task.prompt)
            content = None
            try:
                content = res.get("response", {}).get("content")
            except AttributeError:
                content = None

            chat_id = res.get("chat_id") if isinstance(res, dict) else None
            assistant_message_id = res.get("assistant_message_id") if isinstance(res, dict) else None

            preview = None
            if content:
                preview = content[:500]
            else:
                preview = json.dumps(res, ensure_ascii=False)[:500]

            update_last_response_preview(task_id, preview)

            if task.notify:
                post_notify(
                    task_id=task_id,
                    task_prompt=task.prompt,
                    run_at=task.run_at,
                    content=content or preview or "",
                    chat_id=chat_id,
                    assistant_message_id=assistant_message_id,
                )

            now_exec = datetime.now(timezone.utc)
            if task.recurrence:
                next_run = _next_run(task.run_at, task.recurrence)
                if next_run and (task.recurrence_end is None or next_run <= task.recurrence_end):
                    reschedule_task(
                        task_id=task_id,
                        next_run_at=next_run,
                        status=STATUS_PENDING,
                        executed_at=now_exec,
                    )
                    print(
                        f"[scheduled_tasks] Tarea {task_id} finalizada y reprogramada a {next_run.isoformat()} (recurrence={task.recurrence})"
                    )
                else:
                    update_task_status(task_id, STATUS_COMPLETED, executed_at=now_exec)
                    print(
                        "[scheduled_tasks] Tarea "
                        f"{task_id} finalizada y marcada completed (next_run="
                        f"{next_run.isoformat() if next_run else 'None'}, "
                        f"recurrence_end={task.recurrence_end.isoformat() if task.recurrence_end else 'None'})"
                    )
            else:
                update_task_status(task_id, STATUS_DONE, executed_at=now_exec)
                print(f"[scheduled_tasks] Tarea {task_id} finalizada (status=done, notified={task.notify})")
        except Exception as exc:  # pylint: disable=broad-except
            err_text = f"{exc}"
            trace = traceback.format_exc()
            combined_error = f"{err_text}\n{trace}"
            update_last_error(task_id, combined_error[:2000], executed_at=datetime.now(timezone.utc))
            update_task_status(task_id, STATUS_FAILED, executed_at=datetime.now(timezone.utc))
            print(f"[scheduled_tasks] Tarea {task_id} falló: {err_text}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ejecutor de tareas programadas de Soren.")
    parser.add_argument("--once", action="store_true", help="Ejecuta solo un ciclo y termina (útil para cron/Coolify).")
    parser.add_argument("--sleep", type=int, default=DEFAULT_SLEEP_SECONDS, help="Segundos de espera entre ciclos (modo loop).")
    args = parser.parse_args()

    base_url = DEFAULT_BASE_URL
    token = DEFAULT_API_TOKEN

    print(f"[scheduled_tasks] Iniciando worker. Base URL: {base_url} Sleep: {args.sleep}s Once={args.once}")
    if args.once:
        run_once(base_url, token)
        return 0

    while True:
        try:
            run_once(base_url, token)
        except urllib.error.HTTPError as exc:
            print(f"[scheduled_tasks] HTTPError {exc.code}: {exc.read().decode('utf-8', errors='ignore')}")
        except urllib.error.URLError as exc:
            print(f"[scheduled_tasks] URLError: {exc}")
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[scheduled_tasks] Error inesperado: {exc}\n{traceback.format_exc()}")

        time.sleep(args.sleep)


if __name__ == "__main__":
    sys.exit(main())
