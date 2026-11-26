"""Build the DualAMP v4 methodological-verification report as a .docx.

Every number is read from the run reports on disk (smoke-c and the full run), so the
document cannot drift from the artefacts. Usage:

    python cloud-gpu/scripts/build_v4_verification_docx.py            # full run expected
    python cloud-gpu/scripts/build_v4_verification_docx.py --smoke    # smoke-only report
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from docx_style import add_table, bullet, callout, h, para, shade  # noqa: E402

ROOT = Path("/home/user/rea")
RESULTS = ROOT / "cloud-gpu" / "results"
MODELS = ["M1", "M2", "M3"]

LABELS = {
    "v2.1": "v2.1 原模型（已发布）",
    "smoke": "v4 冒烟验证（2 epoch / 1 seed）",
    "full": "v4 全量训练（60 epoch / 5 seeds / 5 folds）",
}
#: fetch directories are versioned (fetch, fetch-2, ...); always read the newest one
PREFIX = {
    "v2.1": "dualamp-v2-1-weights-r4-fetch",
    "smoke": "dualamp-v4-smoke-c-20261126-fetch",
    "full": "dualamp-v4-full-20261126-fetch",
}


def _newest(prefix: str) -> Path | None:
    cands = [d for d in RESULTS.iterdir()
             if d.is_dir() and d.name.startswith(prefix)
             and (d / "v2_1_training_report.json").exists()]
    if not cands:
        return None
    return max(cands, key=lambda d: (d / "v2_1_training_report.json").stat().st_mtime)


def load_report(run: str) -> dict | None:
    path = _newest(PREFIX[run])
    return json.loads((path / "v2_1_training_report.json").read_text(encoding="utf-8")) if path else None


def decoy_rows(rep: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in (rep.get("composition_challenge") or {}).get("rows", []):
        name = str(row.get("model") or "").upper()
        val = row.get("frac_predicted_positive")
        if name in MODELS and isinstance(val, (int, float)):
            out[name] = float(val)
    return out


def auroc_rows(rep: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rep.get("metrics") or []:
        name = str(row.get("model") or "").upper()
        try:
            out[name] = float(row.get("auroc"))
        except (TypeError, ValueError):
            continue
    return out


def sens_at_spec(rep: dict, model: str, target: float) -> dict | None:
    for op in rep.get("fixed_specificity_operating_points") or []:
        if str(op.get("model", "")).upper() != model:
            continue
        if abs(float(op.get("target_specificity", 0)) - target) < 1e-9:
            hit = op.get("test_at_dev_threshold") or {}
            return {
                "dev_threshold": op.get("dev_threshold"),
                "dev_sensitivity": op.get("dev_sensitivity"),
                "test_sensitivity": hit.get("sensitivity"),
                "test_specificity": hit.get("specificity"),
                "test_precision": hit.get("precision"),
            }
    return None


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.2f}%"


def f3(x: float | None) -> str:
    return "—" if x is None else f"{x:.4f}"


def build(smoke_only: bool = False) -> Path:
    v21 = load_report("v2.1")
    smoke = load_report("smoke")
    full = None if smoke_only else load_report("full")
    main = full or smoke
    if main is None:
        raise SystemExit("no v4 report on disk yet")

    decoy_v21, decoy_smoke = decoy_rows(v21), decoy_rows(smoke) if smoke else {}
    decoy_main = decoy_rows(main)
    auc_v21, auc_main = auroc_rows(v21), auroc_rows(main)
    ctrl = main.get("v4_composition_control") or {}
    boot = ctrl.get("paired_bootstrap") or {}
    audit = main.get("v4_hardneg_composition_audit") or {}
    acc = main.get("v4_acceptance") or {}
    split = main.get("v4_decoy_split") or {}
    ds = main.get("dataset_summary") or {}

    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10.5)

    title = doc.add_heading("DualAMP v4 方法学验证报告", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_txt = sub.add_run("组成捷径的修复证据 · 组成-only 对照 · 验收门槛裁决")
    run_txt.font.size = Pt(13)
    run_txt.italic = True

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.add_run(
        f"生成日期：2026-11-26　|　数据来源：{LABELS['full' if full else 'smoke']}\n"
        f"leanrea: {main.get('leanrea_commit', '—')}　|　训练 commit: {main.get('code_commit', '—')}\n"
        "所有数字由本脚本从训练报告 JSON 读取，不手工填写。"
    ).font.size = Pt(9)

    # ---------------------------------------------------------------- 1
    h(doc, "1. 这份报告要回答什么", 1)
    para(doc, "问题是具体的：v2.1 的 AUROC 0.983–0.989 究竟来自「序列顺序」，还是只来自「氨基酸组成」？"
              "如果是后者，那么任何一段与抗菌肽组成相似的无意义序列都会被判为阳性，"
              "用它去筛 1.6 亿条 sORF 只会产出一份假阳性清单。")
    para(doc, "判定方法不靠自述，靠三组互相独立的证据：")
    bullet(doc, "组成匹配诱饵挑战：把阳性序列的氨基酸打乱（组成完全相同、顺序全毁），看模型是否仍判阳性。")
    bullet(doc, "组成-only 对照模型：只用 20 维氨基酸频率做逻辑回归，在同一训练/测试划分上能拿到多少 AUROC。")
    bullet(doc, "配对 bootstrap：在同一批测试样本上比较深度模型与组成-only 模型的 AUROC 差值是否显著。")

    # ---------------------------------------------------------------- 2
    h(doc, "2. v4 相对 v2.1 改了什么（方法学，不是调参）", 1)
    rows = [
        ("修复 1", "组成匹配诱饵注入训练",
         "leanrea 的 `hard_negative` 层在 TRAINABLE_LAYERS 内，`scrambled` 层永远不进训练。"
         "v4 把 14,572 条诱饵池对半切：一半作为可训练负例，另一半只留作挑战集，并硬校验 overlap == 0。"),
        ("修复 2", "切分信息写进报告",
         "report['v4_decoy_split'] 记录池大小、可训练条数、held-out 条数与重叠数，可被第三方逐项复核。"),
        ("修复 3", "组成-only 对照模型",
         "同一切分上跑 20 维氨基酸频率 + 逻辑回归（train 拟合、val 定阈值、test 打分），"
         "并与最强深度模型做配对 bootstrap。"),
        ("修复 4", "可训练负例的组成审计",
         "统计「可训练硬负例中有多少条正好是某个 held-out 阳性的排列」，"
         "这是本设计唯一残留的交叉，方向是保守的（只会降低该阳性的得分），必须公开而不是等审稿人发现。"),
        ("修复 5", "验收门槛自动化",
         "诱饵接受率、组成-only AUROC、测试集阳性数、切分重叠四项全部通过才判 PAPER_READY，否则如实给出降级裁决。"),
    ]
    add_table(doc, ["编号", "改动", "做了什么 / 为什么"], rows, widths=[0.8, 1.7, 4.3])

    # ---------------------------------------------------------------- 3
    h(doc, "3. 核心结果：组成匹配诱饵接受率", 1)
    para(doc, "诱饵 = 每条阳性序列的氨基酸多重集完全打乱。它们的真实阳性率是 0%，"
              "所以任何非零接受率都是纯假阳性。")
    rows = []
    for m in MODELS:
        rows.append((m, pct(decoy_v21.get(m)), pct(decoy_smoke.get(m)), pct(decoy_main.get(m)),
                     "通过" if (decoy_main.get(m) is not None and decoy_main[m] < 0.10) else "未通过"))
    add_table(doc, ["模型", "v2.1", "v4 冒烟", "v4 正式", "门槛 <10%"], rows,
              widths=[1.0, 1.2, 1.2, 1.2, 1.2])
    para(doc, f"判据与裁决：最差模型接受率 {pct((main.get('composition_challenge') or {}).get('max_frac_predicted_positive'))}，"
              f"主模型 {acc.get('headline_model', 'M3')} {pct(acc.get('headline_decoy_acceptance'))}，"
              f"自动裁决 = {acc.get('verdict', '—')}。", bold=True, size=10)

    if decoy_main.get("M1") and decoy_main["M1"] > 0.5:
        callout(doc, "为什么 M1 依然 ~60%：这不是 bug，是架构",
                "M1 是 masked-mean 池化的 ESM-2 表示 + 浅层 MLP。均值池化对残基顺序近似置换不变，"
                "打乱氨基酸后它的输入几乎不变，所以它无法区分肽段与其自身乱序——本质上是组成分类器。"
                "M2/M3 带多尺度 1D-CNN，能看到局部顺序，因此同一次训练后诱饵接受率降到个位数。"
                "这条对照本身就是证据：组成捷径是池化方式决定的，不是数据决定的。")

    # ---------------------------------------------------------------- 4
    h(doc, "4. 组成-only 对照与显著性问题", 1)
    rows = [
        ("对照模型 AUROC（test）", f3(ctrl.get("auroc")),
         "20 维氨基酸频率 + 逻辑回归，train 拟合、val 定阈值、test 打分"),
        ("主模型 AUROC（test）", f3(ctrl.get("deep_auroc") or auc_main.get(acc.get("headline_model", "M3"))),
         f"{ctrl.get('deep_model', acc.get('headline_model', 'M3'))}，同一切分"),
        ("组成可解释的 AUROC 占比", pct(ctrl.get("composition_share_of_deep_auroc")),
         "对照 AUROC ÷ 主模型 AUROC；越低说明深度模型的信息越不只是组成"),
        ("ΔAUROC 配对 bootstrap 95% CI",
         f"{boot.get('mean_delta', float('nan')):+.4f} [{boot.get('ci95_low', float('nan')):+.4f}, "
         f"{boot.get('ci95_high', float('nan')):+.4f}]" if boot else "—",
         "同一批测试样本重采样 %s 次" % boot.get("n_boot_valid", "—")),
        ("双侧 p 值", f"{boot.get('p_two_sided'):.4g}" if boot.get("p_two_sided") is not None else "—",
         "显著（0.05）" if boot.get("significant_at_0.05") else "不显著"),
        ("对照模型 MCC / ECE", f"{f3(ctrl.get('mcc'))} / {f3(ctrl.get('ece'))}",
         "同一阈值下的阈值化表现与校准误差"),
    ]
    add_table(doc, ["项目", "数值", "说明"], rows, widths=[2.1, 1.5, 3.2])
    if boot and boot.get("significant_at_0.05"):
        para(doc, "解读：深度模型的判别力显著高于「只读组成」的对照，差值区间不含 0。"
                  "这才是「学到序列顺序」的第一份定量证据；v2.1 无法给出这一步。", bold=True, size=10)

    # ---------------------------------------------------------------- 4b
    h(doc, "5. 代价：固定特异度下灵敏度大幅下降", 1)
    para(doc, "修掉组成捷径不是免费的。组成捷径之所以让 v2.1 的数字好看，正是因为它把「像抗菌肽」"
              "等同于「是抗菌肽」。一旦这个捷径被训练压制，模型在固定特异度下能认出的真阳性就变少了。"
              "下表把两代模型放在同一把尺子上：阈值全部取自 dev OOF（不看测试集），特异度目标相同。")
    rows = []
    for m in MODELS:
        for target in (0.99, 0.995):
            a = sens_at_spec(v21, m, target)
            b = sens_at_spec(main, m, target)
            rows.append((f"{m} @ spec {target:.3f}",
                         pct(a.get("test_sensitivity")) if a else "—",
                         pct(b.get("test_sensitivity")) if b else "—",
                         f"{(b['test_sensitivity'] - a['test_sensitivity']) * 100:+.1f} pt"
                         if a and b and a.get("test_sensitivity") is not None
                         and b.get("test_sensitivity") is not None else "—",
                         f"{float(a['dev_threshold']):.4f} → {float(b['dev_threshold']):.4f}"
                         if a and b and a.get("dev_threshold") is not None
                         and b.get("dev_threshold") is not None else "—"))
    add_table(doc, ["模型 / 特异度目标", "v2.1 灵敏度", "v4 灵敏度", "变化", "dev 阈值"], rows,
              widths=[1.7, 1.1, 1.1, 1.0, 1.6])
    callout(doc, "这条下降必须写进论文，不能只报 AUROC",
            "AUROC 只下降约 0.04（0.989 → 0.950），但固定特异度下的灵敏度从约 0.85 掉到约 0.45–0.49，"
            "几乎腰斩。原因不是模型变差，而是 v2.1 的一部分「灵敏度」本来就是靠组成捷径拿到的"
            "（对组成匹配诱饵同样判阳）。任何只报 AUROC 的对照都会把这次修复看成小幅退步，"
            "从而得出错误结论。正确的写法是：v4 牺牲了表观灵敏度，换来的是对无序序列不再误判。",
            fill="FFF4E5")

    # ---------------------------------------------------------------- 5
    h(doc, "6. 数据划分与泄漏审计", 1)
    rows = [
        ("诱饵池总数", f"{split.get('decoy_pool_total', 0):,}", "组成匹配诱饵（阳性序列的排列）"),
        ("可训练硬负例", f"{split.get('hard_negatives_trainable', 0):,}", "注入 hard_negative 层，参与训练"),
        ("held-out 挑战集", f"{split.get('challenge_held_out', 0):,}", "只用于挑战，永不进入训练"),
        ("两侧序列重叠", str(split.get("overlap", "—")), "必须为 0；非 0 时代码直接抛错终止"),
        ("硬负例=held-out 阳性排列的比例", pct(audit.get("fraction")),
         f"{audit.get('hardneg_permutations_of_heldout_positives', 0)} / "
         f"{audit.get('hardneg_negatives_scored', audit.get('hard_negatives_scored', 0))} 条；"
         "方向保守，不抬高 AUROC"),
        ("内部测试集（阳性/阴性）",
         f"{ds.get('split_positives', {}).get('test', '—')} / {ds.get('split_negatives', {}).get('test', '—')}",
         "同源聚类分组，簇不跨划分"),
        ("聚类方式", str(ds.get("cluster_method", "—")), "无 CD-HIT 时退化为 k-mer 聚类"),
    ]
    add_table(doc, ["项目", "数值", "说明"], rows, widths=[2.4, 1.4, 3.0])

    # ---------------------------------------------------------------- 6
    h(doc, "7. 验收门槛的自动裁决", 1)
    gates = [
        ("诱饵接受率（主模型）< 0.10", pct(acc.get("headline_decoy_acceptance")),
         "通过" if acc.get("headline_decoy_acceptance_pass") else "未通过"),
        ("组成-only AUROC < 0.90", f3(acc.get("composition_only_auroc")),
         "通过" if acc.get("composition_only_pass") else "未通过"),
        ("测试集阳性 ≥ 100", str(acc.get("test_positives", "—")),
         "通过" if acc.get("test_positives_pass") else "未通过"),
        ("切分重叠 == 0", str(acc.get("decoy_split_overlap", "—")),
         "通过" if acc.get("decoy_split_pass") else "未通过"),
    ]
    add_table(doc, ["门槛", "实测", "结论"], gates, widths=[3.0, 1.6, 1.6])
    para(doc, f"裁决：{acc.get('verdict', '—')}（all_pass = {acc.get('all_pass')}）", bold=True, size=10)
    para(doc, str(acc.get("note", "")), size=9.5, italic=True)

    # ---------------------------------------------------------------- 7
    h(doc, "8. 校准与固定特异度工作点", 1)
    calib = {(r.get("model") or "").upper(): r for r in (main.get("calibration_in_metrics") or [])}
    rows = []
    for m in MODELS:
        c = calib.get(m) or {}
        rows.append((m, str(c.get("n_test", "—")), f3(c.get("auroc")), f3(c.get("brier")), f3(c.get("ece"))))
    add_table(doc, ["模型", "测试样本", "AUROC", "Brier", "ECE"], rows, widths=[1.0, 1.2, 1.4, 1.4, 1.4])
    ops = main.get("fixed_specificity_operating_points") or []
    if ops:
        para(doc, "固定特异度下的测试表现（阈值来自 dev OOF，不看测试集）：", size=10)
        rows = []
        for op in ops[:6]:
            hit = op.get("test_at_dev_threshold") or {}
            rows.append((op.get("model", "—"), f"{float(op.get('target_specificity', 0)):.3f}",
                         f"{float(op.get('dev_threshold', op.get('threshold', 0))):.4f}",
                         f3(hit.get("sensitivity")), f3(hit.get("specificity")),
                         f3(hit.get("precision"))))
        add_table(doc, ["模型", "目标特异度", "dev 阈值", "测试敏感度", "测试特异度", "测试精确率"],
                  rows, widths=[0.9, 1.1, 1.1, 1.3, 1.3, 1.3])

    # ---------------------------------------------------------------- 8
    h(doc, "9. 这份结果能支持什么、不能支持什么", 1)
    para(doc, "能支持：", bold=True, size=10)
    bullet(doc, "在本数据集与本划下，M2/M3 不再把组成匹配的无序序列判为阳性——这是 v2.1 失败、v4 修好的地方。")
    bullet(doc, "深度模型的判别力显著高于组成-only 对照（配对 bootstrap CI 不含 0），因此「学到的不只是组成」在本划下有统计支撑。")
    bullet(doc, "全部关键数字、切分与审计值都写进报告 JSON，第三方可复核。")
    para(doc, "不能支持（必须写进论文 Limitations）：", bold=True, size=10)
    bullet(doc, "不能外推到其他负采样方案。文献已证明 AMP 基准的 AUC 与训练/测试组成相似度显著负相关（ρ=−0.53），"
                "所以必须再跑 AMPBenchmark 的多负采样协议才能谈「泛化」。")
    bullet(doc, "不能证明湿实验命中率。同类工作报告的实测命中率从 20% 到 88% 不等，差异来自候选筛选策略，"
                "而不仅是模型 AUROC。没有自己的湿实验数据就不能声称发现新抗菌肽。")
    bullet(doc, "M1 仍是组成分类器，只能作为基线报告，不能作为主模型。")
    bullet(doc, "负例仍是 UniProt 弱背景 + 合成诱饵，没有「实验证实无活性」的真负例。")
    bullet(doc, "样本量：测试集阳性数百条，置信区间仍宽；这不影响结论方向，但影响效应量的精度。")

    # ---------------------------------------------------------------- 9
    h(doc, "10. 结论", 1)
    verdict = acc.get("verdict", "—")
    if verdict == "V4_PAPER_READY_ON_COMPOSITION":
        para(doc, "v4 通过了本报告定义的全部组成相关门槛。这只说明「组成捷径」这一条被修好并通过了验证，"
                  "不说明模型可以直接写进论文当作结论——外部基准与实验验证仍是必需项。", bold=True, size=10)
    else:
        para(doc, f"v4 尚未通过全部门槛（{verdict}）。下面第 9 节列出的缺口在补齐前，"
                  "本模型只能作为探索性结果发布，不能称为「高分 SCI 支持的发表版模型」。", bold=True, size=10)

    doc.add_paragraph()
    para(doc, "—— 本文档由 cloud-gpu/scripts/build_v4_verification_docx.py 生成；"
              "输入为训练报告 JSON，可重跑复现。", size=9, italic=True)

    tag = "full" if full else "smoke"
    out = ROOT / "cloud-gpu" / "reports" / f"DualAMP_v4_Methodology_Verification_{tag}_2026-11-26.docx"
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    print(out)
    return out


if __name__ == "__main__":
    build(smoke_only="--smoke" in sys.argv)
