"""Build complete, local-only comparison and a blind human review form."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
from collections import Counter
from pathlib import Path


def raw_answer(record):
    for call in reversed(record["raw_model_calls"]):
        try:
            body = json.loads(call["content"])
        except (ValueError, TypeError):
            continue
        if not isinstance(body, dict):
            continue
        units = body.get("answer_units")
        if isinstance(units, list) and units:
            text = [
                item["text"]
                for item in units
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            if text:
                return "\n\n".join(text)
        if isinstance(body.get("reply"), str) and body["reply"]:
            return body["reply"]
    return ""


def build(root):
    variants = ["main", "before", "candidate"]
    cases = json.loads((root / "cases.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    records = {}
    # Strip auxiliary reasoning from the in-memory report view only. Never
    # relabel raw results or overwrite an existing provenance hash.
    for variant in variants:
        rows = []
        for case in cases:
            for repetition in range(1, 4):
                path = root / "results" / variant / f"{case['id']}-{repetition}.json"
                if not path.exists():
                    raise ValueError(f"Missing required sample: {path}")
                row = json.loads(path.read_text())
                if row.get("guidance"):
                    row["guidance"].pop("reasoning", None)
                expected = manifest["engines"][variant]["tree_sha256"]
                if row.get("engine_sha256") not in {None, expected}:
                    raise ValueError(f"Engine provenance mismatch: {path}")
                row["raw_answer"] = raw_answer(row)
                rows.append(row)
        records[variant] = rows
    for index in range(36):
        for field in ("config_sha256", "case_sha256", "sources_sha256"):
            assert len({records[v][index][field] for v in variants}) == 1, (index, field)

    metrics = {}
    for variant, rows in records.items():
        first = [
            r["first_display_delta_ms"] / 1000
            for r in rows
            if r["first_display_delta_ms"] is not None
        ]
        metrics[variant] = {
            "samples": len(rows),
            "ok": sum(r["status"] == "ok" for r in rows),
            "errors": Counter(r.get("error_type") for r in rows if r["status"] != "ok"),
            "first_display_seconds_median": round(statistics.median(first), 2) if first else None,
            "total_seconds_median": round(statistics.median(r["total_ms"] / 1000 for r in rows), 2),
            "model_calls": dict(Counter(r["model_calls"] for r in rows)),
            "outputs_with_rewrites": sum(bool(r["diagnostics"].get("rewrites")) for r in rows),
            "rewrite_reasons": dict(
                Counter(
                    reason
                    for r in rows
                    for rewrite in r["diagnostics"].get("rewrites", [])
                    for reason in rewrite["reasons"]
                )
            ),
            "outputs_with_metadata_discarded": sum(
                bool(r["diagnostics"].get("metadata_validation")) for r in rows
            ),
            "generic_limitation_outputs": sum(
                "目前資料不足以確認適用的法規版本、期限或法律結論" in r["displayed_reply"]
                for r in rows
            ),
        }
    (root / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# 完整回答比較",
        "",
        "三組各 36 筆，全部保留；以下 raw 只含正式答案文字，不含 provider reasoning。",
        "這是 agent／串流 callback 比較，未量測瀏覽器繪製或正式檢索效能。",
        "",
    ]
    for case in cases:
        lines += [f"## {case['title']}", "", case["message"], ""]
        if case["summary"]:
            lines += ["更正摘要：", "", case["summary"], ""]
        for variant in variants:
            for row in [r for r in records[variant] if r["case_id"] == case["id"]]:
                lines += [
                    f"### {variant} · 第 {row['repetition']} 次 · {row['status']}",
                    "",
                    f"[完整 JSON](results/{variant}/{case['id']}-{row['repetition']}.json)",
                    "",
                    "模型正式答案：",
                    "",
                    "```text",
                    row["raw_answer"] or "（未產生完整正式答案）",
                    "```",
                    "",
                    "實際可顯示文字：",
                    "",
                    "```text",
                    row["displayed_reply"] or row["streamed_reply"] or "（無）",
                    "```",
                    "",
                ]
    (root / "all-responses.md").write_text("\n".join(lines))

    blind, keys = [], {}
    rng = random.Random(20261001)
    for case in cases:
        order = variants.copy()
        rng.shuffle(order)
        keys[case["id"]] = dict(zip(["A", "B", "C"], order, strict=True))
        groups = []
        for label, variant in keys[case["id"]].items():
            answers = []
            for row in [r for r in records[variant] if r["case_id"] == case["id"]]:
                questions = (row.get("guidance") or {}).get("clarification")
                question_text = questions.get("question", "") if isinstance(questions, dict) else ""
                if not question_text:
                    question_text = "\n".join(
                        (row.get("response") or {}).get("clarifying_questions", [])
                    )
                answers.append(
                    {
                        "repetition": row["repetition"],
                        "status": row["status"],
                        "text": row["displayed_reply"] or row["streamed_reply"] or "（未產生回覆）",
                        "question": question_text,
                        "sources": [s["label"] for s in row.get("sources", [])],
                    }
                )
            groups.append({"label": label, "answers": answers})
        blind.append(
            {
                "id": case["id"],
                "title": case["title"],
                "message": case["message"],
                "summary": case["summary"],
                "groups": groups,
            }
        )
    fingerprint = hashlib.sha256(
        json.dumps(blind, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    key_payload = {"schema_version": 1, "dataset_sha256": fingerprint, "cases": keys}
    (root / "blind-answer-key.json").write_text(
        json.dumps(key_payload, ensure_ascii=False, indent=2) + "\n"
    )
    payload = json.dumps(blind, ensure_ascii=False).replace("<", "\\u003c")
    (root / "blind-review.html").write_text(
        HTML.replace("__DATA__", payload).replace("__DATASET__", fingerprint)
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


HTML = """<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>回覆盲評｜12 類案例</title><style>
:root{font-family:system-ui,sans-serif;color:#182a38;background:#f3f5f6;line-height:1.65}body{margin:0}header{background:#152f42;color:white;padding:32px max(24px,4vw)}header p{max-width:950px}main{max-width:1600px;margin:auto;padding:24px}button,select,input,textarea{font:inherit;padding:9px;border:1px solid #b4c1c8;border-radius:6px}button{background:#0c6b77;color:white;cursor:pointer}nav{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}nav button{background:white;color:#183345}nav button.active{background:#0c6b77;color:white}.scenario{background:white;padding:22px;border-radius:10px}.groups{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-top:20px}.group{padding:20px;background:white;border:1px solid #d1d9de;border-radius:10px}h2,h3{line-height:1.3}.answer{white-space:pre-wrap;border-top:1px solid #ddd;padding-top:16px}.meta{font-size:13px;color:#54636c}label{display:block;margin:10px 0}select,textarea{width:100%;box-sizing:border-box}textarea{min-height:90px}.compare{margin-top:20px;background:#e6eef0;padding:18px;border-radius:10px}.status{color:#9b350f}footer{margin-top:24px;padding:20px}#saved{margin-left:16px}@media(max-width:1000px){.groups{grid-template-columns:1fr}}
</style><header><h1>回覆盲評</h1><p>12 類固定合成案例，每個匿名組別完整顯示 3 次回答。請綜合三次表現評分，不挑單次最佳。每題 A／B／C 對應不同版本，順序會變；評分前請勿開啟對照表。</p><p>自然度：是否像順暢的對話；直接性：是否先回應問題；需求滿足：是否提供所需資訊與可行方向。分數 1 低、5 高。法律正確性另由具備專業的人員依來源核對，不能以引用數替代。</p></header>
<main><label>評閱者（選填） <input id="reviewer" placeholder="姓名或代碼"></label><nav id="nav"></nav><div id="case"></div><footer><button id="export">下載評分 JSON</button><span id="saved">尚未提交；僅在本頁保留，重新整理會清空。</span><p>所有評分皆須由人填寫，沒有預設通過；環境設定與正式服務不會被此頁修改。</p></footer></main>
<script>const cases=__DATA__;const dataset='__DATASET__';const ratings={};let active=0;
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function picker(label,key,options,target){const box=el('label',label);const s=el('select');for(const [value,text]of options){const o=el('option',text);o.value=value;s.append(o)}s.value=target[key]||'';s.onchange=()=>target[key]=s.value;box.append(s);return box}
function render(){const c=cases[active];ratings[c.id]??={groups:{},pairs:{},notes:''};const r=ratings[c.id];const nav=document.querySelector('#nav');nav.replaceChildren();cases.forEach((item,i)=>{const b=el('button',`${i+1} ${item.title}`,i===active?'active':'');b.onclick=()=>{active=i;render()};nav.append(b)});const host=document.querySelector('#case');host.replaceChildren();const scenario=el('section',undefined,'scenario');scenario.append(el('h2',`${active+1}. ${c.title}`),el('p',c.message));if(c.summary)scenario.append(el('pre',c.summary,'answer'));host.append(scenario);const groups=el('div',undefined,'groups');for(const g of c.groups){r.groups[g.label]??={};const col=el('section',undefined,'group');col.append(el('h2',`組別 ${g.label}`));for(const a of g.answers){col.append(el('h3',`第 ${a.repetition} 次`));if(a.status!=='ok')col.append(el('p','這次未完成有效回覆，以下可能只有部分文字。','status'));col.append(el('div',a.text,'answer'));if(a.question)col.append(el('p',`追問：${a.question}`));if(a.sources.length)col.append(el('p',`來源標籤：${a.sources.join('、')}`,'meta'))}for(const [key,label]of [['naturalness','自然度'],['directness','直接性'],['needs','需求滿足']])col.append(picker(label,key,[['','待評'],...Array.from({length:5},(_,i)=>[String(i+1),String(i+1)])],r.groups[g.label]));groups.append(col)}host.append(groups);const cmp=el('section',undefined,'compare');cmp.append(el('h3','綜合三次回答後的整體比較'));for(const pair of ['A/B','A/C','B/C'])cmp.append(picker(`${pair[0]} 相較於 ${pair[2]}`,pair,[['','待評'],['better','較好'],['equal','相當'],['worse','較差'],['uncertain','無法判斷']],r.pairs));const note=el('textarea');note.placeholder='具體問題或偏好原因（例如重問、空泛限制、內容缺漏）';note.value=r.notes;note.oninput=()=>r.notes=note.value;cmp.append(note);host.append(cmp)}
document.querySelector('#export').onclick=()=>{const result={schema_version:1,dataset_sha256:dataset,reviewer:document.querySelector('#reviewer').value,reviewed_at:new Date().toISOString(),human_review:true,ratings};const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));a.download='main-alignment-human-ratings.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);document.querySelector('#saved').textContent='已下載本次評分；未填欄位仍為待評。'};render();</script></html>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    build(parser.parse_args().directory)
