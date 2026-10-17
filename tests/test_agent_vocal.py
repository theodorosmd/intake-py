import agent_vocal


def test_sans_proxy_le_llm_est_appele_en_direct(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-6-luna")
    monkeypatch.delenv("LLM_API_BASE", raising=False)
    assert agent_vocal.parametres_llm() == {"model": "openai/gpt-6-luna"}


def test_avec_proxy_le_llm_passe_par_litellm(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "openai/llm-agent")
    monkeypatch.setenv("LLM_API_BASE", "http://localhost:4000")
    monkeypatch.setenv("LLM_API_KEY", "sk-virtuelle")
    assert agent_vocal.parametres_llm() == {
        "model": "openai/llm-agent",
        "api_base": "http://localhost:4000",
        "api_key": "sk-virtuelle",
    }
