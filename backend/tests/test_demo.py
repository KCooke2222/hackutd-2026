"""Hidden demo mode (agent/demo.py): record a model call once, replay it without calling the model."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from paidway.agent import demo


class _Fake(BaseChatModel):
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        msg = AIMessage(content=f"live answer {self.calls}", additional_kwargs={"reasoning_content": "thinking"})
        return ChatResult(generations=[ChatGeneration(message=msg)])


class _Model(demo.ReplayMixin, _Fake):
    pass


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(demo, "DELAY", 0)
    monkeypatch.setattr(demo, "_files", {})
    return _Model()


DATA = "DATA: " + "balance 1200.50 apr 22.5 " * 40


def test_off_calls_the_model(tmp_path, monkeypatch):
    m = _setup(tmp_path, monkeypatch)
    m.invoke("hi")
    m.invoke("hi")
    assert m.calls == 2 and not list(tmp_path.iterdir())


def test_record_then_replay_skips_the_model(tmp_path, monkeypatch):
    m = _setup(tmp_path, monkeypatch)
    msgs = [SystemMessage(DATA + " as of 2026-11-08 plan 0aabe96296b8"), HumanMessage("what is income driven repayment")]
    with demo.session("record", "C001"):
        first = m.invoke(msgs)
    assert m.calls == 1 and (tmp_path / "C001.json").exists()
    demo._files.clear()  # read it back from disk
    msgs2 = [SystemMessage(DATA + " as of 2026-12-08 plan 7b1f1e8a1eb3"), HumanMessage("what is income driven repayment")]
    with demo.session("replay", "C001"):
        again = m.invoke(msgs2)
    assert m.calls == 1
    assert again.content == first.content and again.additional_kwargs.get("reasoning_content") == "thinking"


def test_near_match_on_other_balances_but_not_another_request(tmp_path, monkeypatch):
    m = _setup(tmp_path, monkeypatch)
    with demo.session("record", "C027"):
        m.invoke([SystemMessage(DATA + "\nCustomer's request: Keep 4 months of expenses in savings")])
    with demo.session("replay", "C027"):
        m.invoke([SystemMessage(DATA.replace("1200.50", "1187.10") + "\nCustomer's request: Keep 4 months of expenses in savings")])
        assert m.calls == 1  # same call, balances moved: near match
        m.invoke([SystemMessage(DATA + "\nCustomer's request: Keep 2 months of expenses in savings")])
        assert m.calls == 2  # a different request is a miss: the model is called (and recorded)
        m.invoke([HumanMessage("Keep 3 months")])
        assert m.calls == 3


def test_parse_modes(monkeypatch):
    assert demo.parse("1") == "replay" and demo.parse("0") == "off" and demo.parse("junk") is None
    monkeypatch.setattr(demo, "PUBLIC", True)
    assert demo.parse("record") == "replay"  # visitors on the public deploy can't record
