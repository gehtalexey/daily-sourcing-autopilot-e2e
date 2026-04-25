"""Integration-client tests with HTTP mocked.

Each client (Crustdata, GEM, SalesQL) wraps a third-party API. The risk
surface is uniform: happy-path JSON shape, 4xx errors, 5xx errors, and
network exceptions. These tests pin the client's response-handling so a
schema drift in our code is caught locally instead of in production.
"""

from __future__ import annotations

import pytest
import requests

from integrations.crustdata import CrustdataClient
from integrations.gem import GemClient
from integrations.salesql import SalesQLClient


# ---------------------------------------------------------------------------
# Crustdata
# ---------------------------------------------------------------------------


class TestCrustdataEnrichProfile:
    def test_returns_first_element_when_api_returns_list(self, requests_mock):
        requests_mock.get(
            "https://api.crustdata.com/screener/person/enrich",
            json=[{"linkedin_url": "u", "name": "Ada"}],
            status_code=200,
        )
        client = CrustdataClient(api_key="x")

        result = client.enrich_profile("https://www.linkedin.com/in/ada/")

        assert result == {"linkedin_url": "u", "name": "Ada"}

    def test_returns_object_when_api_returns_dict(self, requests_mock):
        requests_mock.get(
            "https://api.crustdata.com/screener/person/enrich",
            json={"linkedin_url": "u", "name": "Ada"},
            status_code=200,
        )
        client = CrustdataClient(api_key="x")

        assert client.enrich_profile("u")["name"] == "Ada"

    def test_4xx_response_returns_error_dict(self, requests_mock):
        requests_mock.get(
            "https://api.crustdata.com/screener/person/enrich",
            status_code=403,
            text="forbidden",
        )
        client = CrustdataClient(api_key="x")

        result = client.enrich_profile("u")

        assert "error" in result
        assert "403" in result["error"]

    def test_5xx_response_returns_error_dict(self, requests_mock):
        requests_mock.get(
            "https://api.crustdata.com/screener/person/enrich",
            status_code=500,
            text="boom",
        )
        client = CrustdataClient(api_key="x")

        result = client.enrich_profile("u")

        assert "error" in result
        assert "500" in result["error"]

    def test_network_exception_returns_error_dict(self, requests_mock):
        requests_mock.get(
            "https://api.crustdata.com/screener/person/enrich",
            exc=requests.exceptions.ConnectionError("dns down"),
        )
        client = CrustdataClient(api_key="x")

        result = client.enrich_profile("u")

        assert "error" in result
        assert "dns down" in result["error"]


class TestCrustdataSearchPeople:
    def test_returns_profiles_list_on_success(self, requests_mock):
        requests_mock.post(
            "https://api.crustdata.com/screener/person/search",
            json=[{"id": 1}, {"id": 2}],
            status_code=200,
        )
        client = CrustdataClient(api_key="x")

        result = client.search_people(filters=[{"column": "x", "type": "in", "value": [1]}])

        assert result["profiles"] == [{"id": 1}, {"id": 2}]
        assert result["total"] == 2

    def test_4xx_returns_empty_profiles_with_error(self, requests_mock):
        requests_mock.post(
            "https://api.crustdata.com/screener/person/search",
            status_code=400,
            text="bad request",
        )
        client = CrustdataClient(api_key="x")

        result = client.search_people(filters=[])

        assert result["profiles"] == []
        assert result["total"] == 0
        assert "400" in result["error"]


# ---------------------------------------------------------------------------
# GEM
# ---------------------------------------------------------------------------


class TestGemCreateCandidate:
    def _client(self) -> GemClient:
        # Avoid the auto-detect _get_current_user_id call by providing created_by.
        return GemClient(api_key="x", default_project_id="proj-1", created_by="user-1")

    def test_201_returns_success_with_candidate(self, requests_mock):
        requests_mock.post(
            "https://api.gem.com/v0/candidates",
            json={"id": "c-1"},
            status_code=201,
        )

        result = self._client().create_candidate(
            "proj-1", {"first_name": "Ada", "last_name": "Lovelace"}
        )

        assert result["success"] is True
        assert result["candidate"]["id"] == "c-1"

    def test_duplicate_with_existing_id_adds_to_project(self, requests_mock):
        requests_mock.post(
            "https://api.gem.com/v0/candidates",
            status_code=400,
            json={"errors": {"duplicate_candidate": {"id": "c-existing"}}, "message": "dup"},
        )
        requests_mock.put(
            "https://api.gem.com/v0/projects/proj-1/candidates",
            status_code=200,
            json={},
        )

        result = self._client().create_candidate("proj-1", {"first_name": "Ada"})

        assert result["success"] is True
        assert result["candidate_id"] == "c-existing"

    def test_500_returns_failure_with_error(self, requests_mock):
        requests_mock.post("https://api.gem.com/v0/candidates", status_code=500, text="boom")

        result = self._client().create_candidate("proj-1", {"first_name": "Ada"})

        assert result["success"] is False
        assert "500" in result["error"]

    def test_network_exception_returns_failure(self, requests_mock):
        requests_mock.post(
            "https://api.gem.com/v0/candidates",
            exc=requests.exceptions.ConnectionError("dns"),
        )

        result = self._client().create_candidate("proj-1", {"first_name": "Ada"})

        assert result["success"] is False
        assert "dns" in result["error"]


class TestGemRemoveCandidates:
    def _client(self) -> GemClient:
        return GemClient(api_key="x", default_project_id="proj-1", created_by="user-1")

    def test_empty_list_short_circuits(self):
        result = self._client().remove_candidates_from_project("proj-1", [])
        assert result == {"success": True, "removed": 0, "errors": []}

    def test_204_counts_as_removed(self, requests_mock):
        requests_mock.delete(
            "https://api.gem.com/v0/projects/proj-1/candidates",
            status_code=204,
        )

        result = self._client().remove_candidates_from_project("proj-1", ["a", "b", "c"])

        assert result == {"success": True, "removed": 3, "errors": []}

    def test_400_records_error_and_marks_failure(self, requests_mock):
        requests_mock.delete(
            "https://api.gem.com/v0/projects/proj-1/candidates",
            status_code=400,
            text="not in project",
        )

        result = self._client().remove_candidates_from_project("proj-1", ["a"])

        assert result["success"] is False
        assert result["removed"] == 0
        assert any("400" in e for e in result["errors"])


class TestGemCandidateExists:
    def _client(self) -> GemClient:
        return GemClient(api_key="x", default_project_id="proj-1", created_by="user-1")

    def test_returns_false_when_handle_cannot_be_extracted(self):
        assert self._client().candidate_exists("proj-1", "https://example.com/profile") is False

    def test_returns_true_when_candidate_already_in_project(self, requests_mock):
        requests_mock.get(
            "https://api.gem.com/v0/candidates",
            status_code=200,
            json=[{"id": "c-1", "project_ids": ["proj-1", "proj-2"]}],
        )

        result = self._client().candidate_exists("proj-1", "https://www.linkedin.com/in/ada/")

        assert result is True

    def test_returns_false_when_candidate_in_different_project(self, requests_mock):
        requests_mock.get(
            "https://api.gem.com/v0/candidates",
            status_code=200,
            json=[{"id": "c-1", "project_ids": ["proj-2"]}],
        )

        result = self._client().candidate_exists("proj-1", "https://www.linkedin.com/in/ada/")

        assert result is False


# ---------------------------------------------------------------------------
# SalesQL
# ---------------------------------------------------------------------------


class TestSalesQLFindEmail:
    def test_returns_personal_email_on_success(self, requests_mock):
        requests_mock.get(
            "https://api-public.salesql.com/v1/persons/enrich/",
            status_code=200,
            json={"emails": [{"email": "ada@gmail.com", "type": "Direct"}]},
        )

        result = SalesQLClient(api_key="x").find_email("https://linkedin.com/in/ada/")

        assert result["success"] is True
        assert result["email"] == "ada@gmail.com"

    def test_filters_out_non_direct_emails_when_personal_only(self, requests_mock):
        requests_mock.get(
            "https://api-public.salesql.com/v1/persons/enrich/",
            status_code=200,
            json={"emails": [{"email": "ada@work.com", "type": "Work"}]},
        )

        result = SalesQLClient(api_key="x").find_email("u", personal_only=True)

        assert result["success"] is False
        assert result["email"] is None

    def test_404_returns_not_found(self, requests_mock):
        requests_mock.get(
            "https://api-public.salesql.com/v1/persons/enrich/",
            status_code=404,
        )

        result = SalesQLClient(api_key="x").find_email("u")

        assert result["success"] is False
        assert result["error"] == "Not found"

    def test_429_returns_rate_limit_error(self, requests_mock):
        requests_mock.get(
            "https://api-public.salesql.com/v1/persons/enrich/",
            status_code=429,
        )

        result = SalesQLClient(api_key="x").find_email("u")

        assert result["success"] is False
        assert "Rate limit" in result["error"]

    def test_5xx_returns_generic_api_error(self, requests_mock):
        requests_mock.get(
            "https://api-public.salesql.com/v1/persons/enrich/",
            status_code=502,
        )

        result = SalesQLClient(api_key="x").find_email("u")

        assert result["success"] is False
        assert "502" in result["error"]

    def test_network_exception_returns_error(self, requests_mock):
        requests_mock.get(
            "https://api-public.salesql.com/v1/persons/enrich/",
            exc=requests.exceptions.ConnectionError("dns"),
        )

        result = SalesQLClient(api_key="x").find_email("u")

        assert result["success"] is False
        assert "dns" in result["error"]
