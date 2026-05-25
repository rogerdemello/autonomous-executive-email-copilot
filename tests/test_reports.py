import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from env.api import app
from reports.generator import PDFGenerator, PDFReport


class TestPDFGenerator:
    def test_generate_raises_for_missing_episode(self):
        with patch("reports.generator.EpisodeRepository") as MockRepo:
            mock_repo = MagicMock()
            mock_repo.get_episode.return_value = None
            MockRepo.return_value = mock_repo

            gen = PDFGenerator()
            with pytest.raises(ValueError, match="not found"):
                gen.generate("nonexistent_id")

    def test_generate_returns_pdf_bytes(self):
        mock_episode = MagicMock()
        mock_episode.to_dict.return_value = {
            "episode_id": "ep1",
            "task_id": "task1",
            "seed": 42,
            "persona": "balanced",
            "steps": 10,
            "score": 0.85,
            "total_reward": 8.5,
            "breakdown": {"classification": 0.9, "action": 0.8},
            "decisions": [],
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }
        mock_episode.decisions_json = "[]"

        with patch("reports.generator.EpisodeRepository") as MockRepo:
            mock_repo = MagicMock()
            mock_repo.get_episode.return_value = mock_episode
            MockRepo.return_value = mock_repo

            gen = PDFGenerator()
            result = gen.generate("ep1")
            assert isinstance(result, bytes)
            assert len(result) > 0

    def test_generate_summary_with_minimal_data(self):
        gen = PDFGenerator()
        episode_dict = {
            "episode_id": "ep_test",
            "task_id": "hard_full_management",
            "seed": 42,
            "persona": "strict_ceo",
            "steps": 5,
            "score": 0.5,
            "total_reward": 2.5,
            "breakdown": {},
            "decisions": [],
        }
        result = gen.generate_summary(episode_dict)
        assert isinstance(result, bytes)
        assert result.startswith(b"%PDF")

    def test_generate_summary_with_breakdown(self):
        gen = PDFGenerator()
        episode_dict = {
            "episode_id": "ep_breakdown",
            "task_id": "task1",
            "seed": 1,
            "persona": "chill_manager",
            "steps": 3,
            "score": 0.75,
            "total_reward": 7.5,
            "breakdown": {"classification_accuracy": 0.8, "response_quality": 0.7},
            "decisions": [],
        }
        result = gen.generate_summary(episode_dict)
        assert result.startswith(b"%PDF")
        assert len(result) > 500

    def test_generate_summary_with_decisions(self):
        gen = PDFGenerator()
        episode_dict = {
            "episode_id": "ep_decisions",
            "task_id": "task1",
            "seed": 100,
            "persona": "balanced",
            "steps": 2,
            "score": 0.6,
            "total_reward": 6.0,
            "breakdown": {},
            "decisions": [
                {"action_type": "classify", "email_id": "e1", "label": "urgent"},
                {"action_type": "reply", "email_id": "e2", "content": "Thank you for your email."},
            ],
        }
        result = gen.generate_summary(episode_dict)
        assert result.startswith(b"%PDF")
        assert len(result) > 500

    def test_generate_summary_with_timestamps(self):
        gen = PDFGenerator()
        episode_dict = {
            "episode_id": "ep_time",
            "task_id": "task1",
            "seed": 42,
            "persona": "balanced",
            "steps": 1,
            "score": 0.1,
            "total_reward": 1.0,
            "breakdown": {},
            "decisions": [],
            "created_at": "2024-06-15T10:30:00",
            "updated_at": "2024-06-15T11:00:00",
        }
        result = gen.generate_summary(episode_dict)
        assert result.startswith(b"%PDF")
        assert len(result) > 500

    def test_pdf_report_structure(self):
        pdf = PDFReport()
        pdf.add_page()
        pdf.render_episode_summary(
            {
                "episode_id": "ep_struct",
                "task_id": "easy_classification",
                "seed": 42,
                "persona": "balanced",
                "steps": 5,
                "score": 0.9,
                "total_reward": 9.0,
                "breakdown": {"test": 0.5},
                "decisions": [],
            }
        )
        output_bytes = bytes(pdf.output())
        assert output_bytes.startswith(b"%PDF")
        assert b"/Type /Pages" in output_bytes
        assert b"/Type /Font" in output_bytes


class TestReportsAPI:
    @pytest.fixture
    def client(self):
        return TestClient(app)

    @patch("env.api.pdf_generator")
    def test_download_episode_report_success(self, mock_gen, client):
        mock_gen.generate.return_value = b"PDF content here"

        response = client.get("/reports/episode/test_ep_123")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert "attachment" in response.headers["content-disposition"]
        mock_gen.generate.assert_called_once_with("test_ep_123")

    @patch("env.api.pdf_generator")
    def test_download_episode_report_not_found(self, mock_gen, client):
        mock_gen.generate.side_effect = ValueError("Episode not found")

        response = client.get("/reports/episode/nonexistent")

        assert response.status_code == 404

    @patch("env.api.pdf_generator")
    def test_generate_report_from_data(self, mock_gen, client):
        mock_gen.generate_summary.return_value = b"PDF bytes"

        payload = {
            "episode_data": {
                "episode_id": "ep_api",
                "task_id": "task1",
                "seed": 42,
                "persona": "balanced",
                "steps": 10,
                "score": 0.8,
                "total_reward": 8.0,
            }
        }

        response = client.post("/reports/generate", json=payload)

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        mock_gen.generate_summary.assert_called_once_with(payload["episode_data"])