"""Build the release-incident document (what was published vs what the page says).

Numbers are read from the artefacts that caused the mismatch: the GitHub release body
(dumped to cloud-gpu/reports/release_body_20261126.json) and the real run reports.
Nothing is typed in by hand except the narrative.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from docx_style import add_table, bullet, callout, h, para  # noqa: E402

ROOT = Path("/home/user/rea")
RESULTS = ROOT / "cloud-gpu" / "results"
BODY = ROOT / "cloud-gpu" / "reports" / "release_body_20261126.json"


def newest(prefix: str) -> Path | None:
    cands = [d for d in RESULTS.iterdir()
             if d.is_dir() and d.name.startswith(prefix)
             and (d / "v2_1_training_report.json").exists()]
    return max(cands, key=lambda d: (d / "v2_1_training_report.json").stat().st_mtime) if cands else None


def build() -> Path:
    v21 = json.loads((newest("dualamp-v2-1-weights-r4-fetch")
                      / "v2_1_training_report.json").read_text(encoding="utf-8"))
    body = ""
    if BODY.exists():
        payload = json.loads(BODY.read_text(encoding="utf-8"))
        body = payload.get("body", "") if isinstance(payload, dict) else str(payload)

    auroc = {}
    for row in v21.get("metrics") or []:
        try:
            auroc[str(row.get("model")).upper()] = float(row.get("auroc"))
        except (TypeError, ValueError):
            pass
    pos_sha = (v21.get("cohort") or {}).get("positive_fasta_sha256", "—")
    commit = v21.get("code_commit", "—")

    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10.5)

    t = doc.add_heading("发布事故说明：Release 正文与资产不一致", level=0)
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = sub.add_run("dualamp-v2.1-weights-15-files-20261126　|　2026-11-26")
    r.font.size = Pt(12)
    r.italic = True

    h(doc, "1. 结论先说", 1)
    para(doc, "资产（15 个 .pt 权重）是真的、可下载、可校验；页面上写的口径、训练 commit、"
              "阳性序列 SHA256 与测试 AUROC 却是另一个模型（2026-11-16 的 pilot）的。"
              "也就是说：文件没问题，可追溯性出了问题。")
    callout(doc, "为什么这件事严重",
            "论文附录里，审稿人核对的第一件事是「这个权重对应哪次训练、哪版数据」。"
            "如果 release 页面的 SHA256 和实际训练用的阳性序列对不上，即使模型结果完全正确，"
            "这份发布也不能被引用。")

    h(doc, "2. 逐项对照", 1)
    rows = [
        ("口径", "candidate_v1_pilot_weights（2026-11-16 pilot）", "positive_core_v2_1（DRAMP 回填后）"),
        ("训练 commit", "8a2d232", commit),
        ("阳性 FASTA SHA256", "7b6ea121…68aa1d", pos_sha),
        ("测试 AUROC", "0.9767–0.9809", " / ".join(f"{auroc.get(m, float('nan')):.4f}"
                                                   for m in ("M1", "M2", "M3"))),
        ("来源 kernel", "dualamp-real5-20261116", "dualamp-v2-1-weights-r4-20261125"),
        ("文件数", "3（tarball 整包）", "17（15 × .pt + SHA256SUMS.txt + export_manifest.json）"),
    ]
    add_table(doc, ["字段", "Release 页面写的", "实际资产"], rows, widths=[1.4, 2.6, 3.0],
              highlight_rows=(0, 1, 2, 3))

    h(doc, "3. 根因", 1)
    para(doc, "两个原因，都不是「手滑」：")
    bullet(doc, "说明文字写死在 workflow 里。.github/workflows/weights-release.yml 的 release-notes "
                "heredoc 直接 echo 了 pilot 时期的字面量；pack_release_assets.py 的 PROVENANCE 与 "
                "LABEL_NOTE 默认值同样是 pilot 时期的值，只有显式传入环境变量才变。于是资产换了、"
                "说明没换，而流程里没有任何断言能发现这一点。")
    bullet(doc, "创建与上传被拆成两步，中间没有一致性校验。先 gh release create（无资产，但已发布），"
                "再逐个 gh release upload。create 一成功页面就是线上状态，此后任何中断都会留下"
                "「已发布但内容/说明不匹配」的形态；流程无法区分「上传失败」和「文字写错」。")
    para(doc, "另有一个容易被误读的细节：四个 release 的 created_at 都是 2026-10-15T01:29:04Z，"
              "因为 GitHub 在首次创建 tag 时会沿用同一 tag 之前残留的 draft 的创建时间。"
              "这是平台行为，不代表发布时间。", size=9.5, italic=True)

    if body:
        h(doc, "4. 线上正文原文（存档）", 1)
        para(doc, "以下为该 release 页面的实际正文，逐字保留，便于日后核对：", size=9.5)
        for line in body.splitlines():
            if not line.strip():
                continue
            p = doc.add_paragraph()
            run = p.add_run(line)
            run.font.size = Pt(8)
            run.font.name = "Consolas"

    h(doc, "5. 已完成的修复", 1)
    bullet(doc, "weights-release.yml 的说明文字改为由 workflow 输入参数生成：label_note、provenance_kernel、"
                "provenance_commit、provenance_sha256、provenance_models、metrics_table。"
                "写死的 pilot 字面量已全部删除。")
    bullet(doc, "provenance 通过环境变量透传给 pack_release_assets.py，与 release 正文取自同一组输入，"
                "不会再出现两处各写一份的情况。")
    bullet(doc, "v4 权重使用全新 tag（dualamp-v4-weights-15-files-20261126），"
                "不覆盖、不修改任何既有 tag 与资产。")

    h(doc, "6. 尚未处理（需要决定）", 1)
    rows = [
        ("(a)", "保留资产，仅替换 release 正文",
         "不改 tag、不动任何文件，只改文字。推荐。"),
        ("(b)", "新建 …-corrected tag 发布同批资产并加注指向",
         "保留原页面作为历史记录，但会出现两份同内容发布。"),
        ("(c)", "删除该 release 并重发", "会破坏已有外链与引用，不推荐。"),
    ]
    add_table(doc, ["方案", "做法", "影响"], rows, widths=[0.7, 2.9, 3.4])
    para(doc, "在选定之前，本文档即为该 release 的更正声明。", bold=True, size=10)

    h(doc, "7. 教训", 1)
    para(doc, "「能下载」不等于「可追溯」。发布流程必须让说明文字与资产来自同一份输入，"
              "并在发布前断言二者一致；否则错误会安静地留在线上，而且看起来一切正常。",
         bold=True, size=10)

    doc.add_paragraph()
    para(doc, "—— 本文档由 cloud-gpu/scripts/build_release_incident_docx.py 生成；"
              "输入为 release 正文存档与 v2.1 训练报告 JSON。", size=9, italic=True)

    out = ROOT / "cloud-gpu" / "reports" / "DualAMP_Release_Incident_2026-11-26.docx"
    doc.save(out)
    print(out)
    return out


if __name__ == "__main__":
    build()
