import json
from datetime import datetime
from io import BytesIO

from fpdf import FPDF

from env.repositories import EpisodeRepository


class PDFGenerator:
    def __init__(self):
        self.repo = EpisodeRepository()

    def generate(self, episode_id: str) -> bytes:
        episode = self.repo.get_episode(episode_id=episode_id)
        if episode is None:
            raise ValueError(f"Episode {episode_id} not found")
        episode_dict = episode.to_dict()
        if episode.decisions_json:
            episode_dict["decisions"] = json.loads(episode.decisions_json)
        return self.generate_summary(episode_dict)

    def generate_summary(self, episode_dict: dict) -> bytes:
        pdf = PDFReport()
        pdf.add_page()
        pdf.render_episode_summary(episode_dict)
        result = pdf.output()
        return bytes(result)


class PDFReport(FPDF):
    def header(self):
        self.set_font("helvetica", "B", 16)
        self.cell(0, 10, "Episode Report", border=False, new_x="LMARGIN", new_y="NEXT", align="C")
        self.ln(5)

    def section_title(self, title: str):
        self.set_font("helvetica", "B", 12)
        self.set_fill_color(200, 220, 255)
        self.cell(0, 8, title, border=True, new_x="LMARGIN", new_y="NEXT", fill=True)
        self.ln(2)

    def key_value(self, key: str, value: str):
        self.set_font("helvetica", "B", 10)
        self.cell(50, 6, key, border=False)
        self.set_font("helvetica", "", 10)
        self.cell(0, 6, str(value), border=False, new_x="LMARGIN", new_y="NEXT")

    def render_episode_summary(self, episode: dict):
        self.set_font("helvetica", "", 10)
        self.section_title("Episode Overview")
        self.key_value("Episode ID:", episode.get("episode_id", "N/A"))
        self.key_value("Task ID:", episode.get("task_id", "N/A"))
        self.key_value("Seed:", str(episode.get("seed", "N/A")))
        self.key_value("Persona:", episode.get("persona", "N/A"))
        self.key_value("Steps:", str(episode.get("steps", "N/A")))
        self.key_value("Score:", f"{episode.get('score', 0):.4f}")
        self.key_value("Total Reward:", f"{episode.get('total_reward', 0):.4f}")
        self.ln(5)

        self.section_title("Score Breakdown")
        breakdown = episode.get("breakdown", {})
        if breakdown:
            for key, value in breakdown.items():
                self.key_value(f"{key}:", f"{value:.4f}")
        else:
            self.set_font("helvetica", "", 10)
            self.cell(0, 6, "No breakdown available", new_x="LMARGIN", new_y="NEXT")
        self.ln(5)

        self.section_title("Action Timeline")
        decisions = episode.get("decisions", [])
        if decisions:
            self.set_font("courier", "", 9)
            for i, decision in enumerate(decisions, 1):
                action_type = decision.get("action_type", "N/A")
                email_id = decision.get("email_id", "")
                label = decision.get("label", "")
                content = decision.get("content", "")
                self.cell(0, 5, f"Step {i}: {action_type}", new_x="LMARGIN", new_y="NEXT")
                if email_id:
                    self.cell(0, 5, f"  Email: {email_id}", new_x="LMARGIN", new_y="NEXT")
                if label:
                    self.cell(0, 5, f"  Label: {label}", new_x="LMARGIN", new_y="NEXT")
                if content:
                    content_preview = content[:80] + "..." if len(content) > 80 else content
                    self.cell(0, 5, f"  Content: {content_preview}", new_x="LMARGIN", new_y="NEXT")
        else:
            self.set_font("helvetica", "", 10)
            self.cell(0, 6, "No decisions recorded", new_x="LMARGIN", new_y="NEXT")
        self.ln(5)

        self.section_title("Telemetry")
        self.key_value("Created:", episode.get("created_at", "N/A"))
        self.key_value("Updated:", episode.get("updated_at", "N/A"))