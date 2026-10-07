import os
import uuid
import json
import threading
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

from pydantic import BaseModel, field_validator
from sqlalchemy import create_engine, Column, Text, DateTime, String
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

from src.planning_agent import planner_agent, executor_agent_step
from src.plan_logic import DEFAULT_AGENT_MODEL, DEFAULT_PLANNER_MODEL, HistoryEntry

# === Load env vars ===
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

# Fix for Heroku's postgres:// URL format
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


# === DB setup ===
Base = declarative_base()
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine)


class Task(Base):
    __tablename__ = "tasks"
    id = Column(String, primary_key=True, index=True)
    prompt = Column(Text)
    status = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    result = Column(Text)


try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    print(f"DB creation failed: {e}")

# Tasks still "running" belong to worker threads of a previous process, which
# are gone; mark them as failed so they don't stay "running" forever.
try:
    db = SessionLocal()
    db.query(Task).filter(Task.status == "running").update(
        {"status": "error", "updated_at": datetime.utcnow()}
    )
    db.commit()
    db.close()
except Exception as e:
    print(f"Failed to reset interrupted tasks: {e}")

# === FastAPI ===
app = FastAPI()
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

task_progress = {}


class PromptRequest(BaseModel):
    prompt: str
    model: Optional[str] = None  # research/writer/editor agents
    planner_model: Optional[str] = None

    @field_validator("model", "planner_model")
    @classmethod
    def check_model_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        provider, sep, name = v.partition(":")
        if not sep or not provider or not name:
            raise ValueError("model must be in 'provider:model' form, e.g. 'openai:gpt-4o'")
        return v

@app.get("/", response_class=JSONResponse)
def health_check():
    return {"status": "ok"}


@app.post("/generate_report")
def generate_report(req: PromptRequest):
    task_id = str(uuid.uuid4())
    db = SessionLocal()
    db.add(Task(id=task_id, prompt=req.prompt, status="running"))
    db.commit()
    db.close()

    model = req.model or DEFAULT_AGENT_MODEL
    planner_model = req.planner_model or DEFAULT_PLANNER_MODEL

    task_progress[task_id] = {"steps": []}
    try:
        initial_plan_steps = planner_agent(req.prompt, model=planner_model)
    except Exception as exc:
        db = SessionLocal()
        task = db.query(Task).filter(Task.id == task_id).first()
        task.status = "error"
        task.updated_at = datetime.utcnow()
        db.commit()
        db.close()
        raise HTTPException(status_code=502, detail=f"Planner failed: {exc}")

    for step_title in initial_plan_steps:
        task_progress[task_id]["steps"].append(
            {
                "title": step_title,
                "status": "pending",
                "description": "Awaiting execution",
                "sub-steps": [],
            }
        )

    thread = threading.Thread(
        target=run_agent_workflow,
        args=(task_id, req.prompt, initial_plan_steps, model, planner_model),
    )
    thread.start()
    return {"task_id": task_id}


@app.get("/task_progress/{task_id}")
def get_task_progress(task_id: str):
    return task_progress.get(task_id, {"steps": []})


@app.get("/task_status/{task_id}")
def get_task_status(task_id: str):
    db = SessionLocal()
    task = db.query(Task).filter(Task.id == task_id).first()
    db.close()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "status": task.status,
        "result": json.loads(task.result) if task.result else None,
    }

@app.get("/report/{task_id}/md")
def get_markdown(task_id: str):
    file_path = f"/app/reports/{task_id}.md"

    if not os.path.exists(file_path):
        return {"error": "Markdown file not found"}

    return FileResponse(
        file_path,
        media_type="text/markdown",
        filename=f"{task_id}.md"
    )


def format_history(history):
    return "\n\n".join(
        f"🔹 {title}\n{agent}\n\n📝 Output:\n{output}" for title, agent, output in history
    )


def run_agent_workflow(
    task_id: str,
    prompt: str,
    initial_plan_steps: list,
    model: str = DEFAULT_AGENT_MODEL,
    planner_model: str = DEFAULT_PLANNER_MODEL,
):
    print(f"[START] Task {task_id}")
    steps_data = task_progress[task_id]["steps"]
    execution_history = []

    def update_step_status(index, status, description="", substep=None):
        if index < len(steps_data):
            steps_data[index]["status"] = status
            if description:
                steps_data[index]["description"] = description
            if substep:
                steps_data[index]["sub-steps"].append(substep)
            steps_data[index]["updated_at"] = datetime.utcnow().isoformat()

    try:
        for i, plan_step_title in enumerate(initial_plan_steps):
            update_step_status(i, "running", f"Executing: {plan_step_title}")

            actual_step_description, agent_name, output = executor_agent_step(
                plan_step_title, execution_history, prompt, model=model
            )

            execution_history.append(HistoryEntry(plan_step_title, agent_name, output))

            # ...
            update_step_status(
                i,
                "done",
                f"Completed: {plan_step_title}",
                {
                    "title": f"Called {agent_name}",
                    "content": f"""
<div style='border:1px solid #ccc; border-radius:8px; padding:10px; margin:8px 0; background:#fff;'>
  <div style='font-weight:bold; color:#2563eb;'>📘 User Prompt</div>
  <div style='white-space:pre-wrap;'>{prompt}</div>

  <div style='font-weight:bold; color:#16a34a; margin-top:8px;'>📜 Previous Step</div>
  <pre style='white-space:pre-wrap; background:#f9fafb; padding:6px; border-radius:6px; margin:0;'>
{format_history(execution_history[-2:-1])}
  </pre>

  <div style='font-weight:bold; color:#f59e0b; margin-top:8px;'>🧹 Your next task</div>
  <div style='white-space:pre-wrap;'>{actual_step_description}</div>

  <div style='font-weight:bold; color:#10b981; margin-top:8px;'>✅ Output</div>
  <!-- ⚠️ NO <pre> here -->
  <div style='white-space:pre-wrap;'>
{output}
  </div>
</div>
""".strip(),
                },
            )

        final_report_markdown = (
            execution_history[-1].output if execution_history else "No report generated."
        )

        result = {
            "html_report": final_report_markdown,
            "history": steps_data,
            "model": model,
            "planner_model": planner_model,
        }

        print(f"[DONE] Task {task_id}, saving result to database")
        db = SessionLocal()
        task = db.query(Task).filter(Task.id == task_id).first()
        task.status = "done"
        task.result = json.dumps(result)
        task.updated_at = datetime.utcnow()
        db.commit()
        db.close()
        print(f"[SUCCESS] Task {task_id} saved to database")

        print(f"Saving report to app volume")
        os.makedirs("/app/reports", exist_ok=True)

        md_path = f"/app/reports/{task_id}.md"

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(final_report_markdown)

        print(f"[FILE SAVED] {md_path}")

    except Exception as exc:
        print(f"Workflow error for task {task_id}: {exc}")
        if steps_data:
            error_step_index = next(
                (i for i, s in enumerate(steps_data) if s["status"] == "running"),
                len(steps_data) - 1,
            )
            if error_step_index >= 0:
                update_step_status(
                    error_step_index,
                    "error",
                    f"Error during execution: {exc}",
                    {"title": "Error", "content": str(exc)},
                )

        db = SessionLocal()
        task = db.query(Task).filter(Task.id == task_id).first()
        task.status = "error"
        task.updated_at = datetime.utcnow()
        db.commit()
        db.close()
