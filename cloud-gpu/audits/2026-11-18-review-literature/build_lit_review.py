#!/usr/bin/env python3
# 2026-11-18 literature-backed verification pass for positive_core_qc_review.tsv (230 rows).
# Rules (user 2026-11-18): CONFIRMED requires specific literature (PMID/DOI) support;
# rows without citable literature must NOT be marked CONFIRMED.
# This script does NOT decide scientific truth of every MIC value; it verifies that each cited PMID
# exists, is indexed, and concerns antimicrobial peptide activity (esummary/web_search, 2026-11-18),
# plus that the record itself attributes its claimed activity to that Ref ([Ref.NNN] in record text).
import csv, json, re, sys, datetime
SRC = '/tmp/review_qc.tsv'
NOW = '2026-11-18'
# pmid -> (journal_year, ok, short_note). ok=True => on-topic antimicrobial peptide literature.
PM = {
 '10430870':('PNAS 1999',True,'Tam et al., end-to-end macrocycle cystine-knot AMP motif (kalata B1 cyclotide family)'),
 '10651828':('Eur J Biochem 2000',True,'AMPs from skins of North American frogs Rana luteiventris/berlandieri/pipiens'),
 '10660589':('J Biol Chem 2000',True,'Feder/Mor dermaseptin S4 analog structure-activity (selective cytotoxicity)'),
 '10952593':('Antimicrob Agents Chemother 2000',True,'Mor dermaseptin S4 derivatives antimalarial'),
 '11479030':('Biochim Biophys Acta 2001',True,'dicynthaurin tunicate hemocyte AMP'),
 '11738090':('Biochim Biophys Acta 2001',True,'Ali et al. Xenopus tropicalis skin AMPs'),
 '11847217':('J Biol Chem 2002',True,'Kustanovich/Mor S4 analogs vs bacteria/protozoa/RBC'),
 '12372834':('J Biol Chem 2002',True,'Boulanger Stomoxyn: fly gut AMP with antiparasitic activity'),
 '12429503':('Biochim Biophys Acta 2002',True,'Ali crawfish frog Rana areolata AMPs'),
 '15207717':('Biochem Biophys Res Commun 2004',True,'ascaphins, Ascaphus truei skin AMPs'),
 '15225564':('Toxicon 2004',True,'Agelaia pallipes protonectin/agelaia-MP1'),
 '15273109':('Antimicrob Agents Chemother 2004',True,'Guerrero magainin 2 leishmanicidal structure-activity'),
 '15317499':('J Pept Res 2004',True,'eumenine mastoparan-AF wasp venom AMP'),
 '15328096':('Antimicrob Agents Chemother 2004',True,'Frecer de novo design of potent AMPs'),
 '15752569':('Peptides 2004/2005',True,'phylloseptins anti-bacterial/anti-protozoan Phyllomedusa'),
 '15996769':('Regul Pept 2005',True,'Conlon Rana virgatipes skin AMPs'),
 '16460023':('Biochemistry 2006',True,'trialysin-derived amphipathic peptides lytic activity'),
 '16735513':('J Biol Chem 2006',True,'Kozlov latarcins Lachesana tarabaevi spider venom AMPs'),
 '1744108':('J Biol Chem 1991',True,'Gibson bombinin-like AMPs Bombina orientalis'),
 '18008336':('Biopolymers 2008',True,'Ireland & Craik cyclotides as anti-HIV agents (antiviral AMP; adjunct ref)'),
 '18375018':('Peptides 2008',True,'Polistinae wasp venom AMPs and analogs'),
 '18554256':('Chem Biol Drug Des 2008',True,'temporin-1CEa-based design (antitumor-tilted AMP analogs)'),
 '19028675':('Mol Cell Proteomics 2009',False,'Yang antioxidant peptidomics (antioxidant system; NOT antimicrobial-activity evidence)'),
 '19109988':('Toxicon 2010',True,'Stocklin Osmia rufa osmin; DRAMP03007/APD AP02882'),
 '19111587':('Peptides 2009',True,'green coconut water Cn-AMPs'),
 '19340924':('Biochimie 2009',True,'Hylarana latouchii brevinin-1/temporin'),
 '19635516':('Peptides 2009',True,'Conlon Lithobates capito/warszewitschii brevinins/esculentins'),
 '19843479':('Comp Biochem Physiol B 2010',True,'Wang Amolops loloensis five novel AMPs'),
 '20198492':('Amino Acids 2010',True,'Halictus sexcinctus bee venom AMPs and analogs'),
 '21262294':('Peptides 2011',True,'Perez-Cordero leishmanicidal peptides (antiparasitic)'),
 '21703315':('Peptides 2011',True,'Dolashka Rapana venosa proline-rich AMPs'),
 '22029824':('J Proteome Res 2012',True,'Yang Chinese odorous frogs skin AMPs'),
 '22982494':('Biochim Biophys Acta 2012',True,'HPA3NT3 analogs cell selectivity/mechanism'),
 '23000095':('Peptides 2012',True,'Nie H. spinifer HsAp1-4'),
 '23894079':('ChemMedChem 2013',True,'ascaphin-8/XT-7 beta-hairpin AMP design'),
 '24172540':('Peptides 2013',True,'Mechkarska Hymenochirin-1B analogs'),
 '25056849':('Chem Biol Drug Des 2015',True,'feleucin-BO1/-K3 Bombina orientalis AMPs'),
 '25312021':('FEBS J 2015',True,'Shigeri Xenopus tropicalis skin novel peptides MALDI-MS'),
 '25494332':('(PMC4262413) 2014',True,'Tryptophan->Pseudomonas-targeted amphipathic peptide'),
 '25753638':('Antimicrob Agents Chemother 2015',True,'Wang P3/JH-3 bovine-derived AMP membrane mechanism'),
 '25818457':('Biomaterials 2015',True,'Ma et al. AMP membrane-disruption study (Biomaterials 52:517-530)'),
 '26883699':('Antimicrob Agents Chemother 2016',True,'Marr host defense peptides leishmanicidal library'),
 '2689223':('FEBS Lett 1989',True,'Boman cecropin-melittin hybrids antibacterial/antimalarial'),
 '26998557':('(2016)',True,'Straka Hylaeus signatus bee venom AMPs and synergy with antibiotics'),
 '27251456':('Sci Rep 2016',True,'Lyu PMAP-36-derived RI18'),
 '2759705':('Infect Immun 1989',True,'Gwadz magainins/cecropins against Plasmodium (anti-parasitic)'),
 '27818338':('Fish Shellfish Immunol 2016',True,'salmonid cathelicidin Gly/Ser-rich AMPs'),
 '27912176':('Eur J Med Chem 2017',True,'Pedron VmCT1 analogs'),
 '28073163':('Biopolymers 2017',True,'hydrocarbon-stapled lipopeptides'),
 '28258530':('Biotechnol Lett 2017',True,'catesbeianin-1 American bullfrog AMP'),
 '28741926':('ACS Chem Biol 2017',True,'Henriques redesigned cyclic gomesin spider peptide'),
 '28960954':('ACS Infect Dis 2017',True,'Edwards tachyplesin-1 SAR/toxicity'),
 '29040295':('PLoS One 2017',True,'Chang TP4 helix determinants'),
 '29043494':('Probiotics Antimicrob Proteins 2018',True,'Yan NK-2 proline substitution'),
 '29183811':('Fish Shellfish Immunol 2018',True,'TO17 teleost AMP nucleic-acid degradation'),
 '29266746':('Chem Biol Drug Des 2018',True,'Dong pig nematode CP-1 N-terminal fragments'),
 '29402952':('Sci Rep 2018',True,'Jodoin Histone H5 antimicrobial H5 template'),
 '29501691':('Peptides 2018',True,'Duwadi arthropod genomes AMP screening'),
 '30538681':('(2018, PMC6277555)',True,'short symmetric-end AMPs beta-turn (PQ/PP)'),
 '30789695':('ACS Chem Biol 2019',True,'glycosylation strategy on Hymenochirin-1B (membrane strategy)'),
 '31001677':('Parasitol Res 2019',True,'Tonk Metchnikowin Mtk-1/Mtk-2 anti-Plasmodium'),
 '31163671':('(2019, PMC6631481)',True,'Pirtskhalava de novo designed AMPs vs Gram-negative'),
 '31181304':('Eur J Pharm Sci 2019',True,'Pedron VmCT1 lysine substitutions'),
 '31212183':('Bioorg Chem 2019',True,'Pedron VmCT1 repurposed vs ESKAPE'),
 '31276398':('J Med Chem 2019',True,'Lyu Trp-rich dodecapeptides broad-spectrum'),
 '31549575':('(2019)',True,'Brevinin-GR23 Hylarana guentheri anti-S. aureus/biofilm'),
 '31940927':('Mar Drugs 2020',True,'Haug Synoicum turgens turgencin A/B disulfide AMPs'),
 '32439180':('Biochem Biophys Res Commun 2020',True,'PAMP/PAMP(9-20) cell selectivity'),
 '32751755':('Mar Drugs 2020',True,'Haug turgencin A-derived synthetic AMPs'),
 '33285267':('Eur J Pharm Sci 2021',True,'Zhu designed AMPs vs rifampin-resistance reversal'),
 '33291521':('Molecules 2020',True,'Wei temporin-like anti-S. mutans antibiofilm'),
 '34206990':('Viruses 2021',True,'Xu human defensins block SARS-CoV-2 entry'),
 '7886453':('Science 1995',True,'Schonwetter/Zasloff epithelial antibiotics at inflammation sites'),
 '8577744':('Proc Natl Acad Sci USA 1996',True,'Fehlbaum thanatin 21-residue insect defense peptide'),
 '8601432':('(1995)',True,'broad-spectrum antibiotic skin-PYY'),
}
rows = list(csv.DictReader(open(SRC), delimiter='\t'))
chan = 'eutils esummary db=pubmed + web_search, fetched 2026-11-18 (title/journal/year level; MIC full text not re-measured)'
out_rows = []
counts = {'CONFIRMED_CORE':0,'UNSURE_NEEDS_LITERATURE':0}
issues = []
for r in rows:
    res = dict(r)
    cand = (r['Citation_Candidates'] or '').strip()
    if not cand:
        res['Review_Result']='UNSURE_NEEDS_LITERATURE'
        res['Review_Reason']=f'[LIT-{NOW}] Citation_Candidates empty; no specific literature to verify; must not be CONFIRMED (user 2026-11-18 rule).'
        counts['UNSURE_NEEDS_LITERATURE']+=1
        out_rows.append(res); continue
    pmids = [c for c in re.split(r'[;,|/\s]+', cand) if c.strip().isdigit()]
    missing = [p for p in pmids if p not in PM]
    if missing:
        issues.append((r['Sequence_ID'], 'PMID not verified in pass: '+','.join(missing)))
        res['Review_Result']='UNSURE_NEEDS_LITERATURE'
        res['Review_Reason']=f'[LIT-{NOW}] cited PMID(s) not verified in this pass: {",".join(missing)}; treat as unverified.'
        counts['UNSURE_NEEDS_LITERATURE']+=1
        out_rows.append(res); continue
    flags = [p for p in pmids if not PM[p][1]]
    if flags:
        res['Review_Result']='UNSURE_NEEDS_LITERATURE'
        res['Review_Reason']=f'[LIT-{NOW}] cited PMID on record but does not evidence the antimicrobial claim: '+', '.join(f'{p} ({PM[p][0]}: {PM[p][2]})' for p in flags)+'. Needs replacement citation or PI sign-off; not CONFIRMED.'
        counts['UNSURE_NEEDS_LITERATURE']+=1
        out_rows.append(res); continue
    # special: antiprotozoal-only records should not pass as antibacterial-core silently; keep CONFIRMED but flag text
    note = ', '.join(f'PMID {p} ({PM[p][0]})' for p in pmids)
    extra=''
    if r['Sequence_ID']=='AMP_009038':
        extra=' NOTE: record claims antiprotozoal only; cited refs are leishmanicidal/anti-Plasmodium; antibacterial-core suitability NOT established (user/PI decision; draft suggested REJECT_NOT_ANTIBACTERIAL).'
    elif r['Sequence_ID']=='AMP_000243':
        extra=' NOTE: primary ref 10430870 = anti-HIV/cyclotide motif; claimed MIC table not re-read in full text.'
    blob = (r['Activity_Description'] or '') + ' || ' + (r['Target_Organism'] or '')
    refs = set(re.findall(r'Ref[^0-9]{0,6}(\d{5,9})', blob))
    in_rec = any(p in refs for p in pmids)
    res['Review_Result']='CONFIRMED_CORE' if (r['Sequence_ID']!='AMP_009038' and in_rec) else 'UNSURE_NEEDS_LITERATURE'
    src = 'record text attributes claimed activity to cited ref ([Ref.NNN] present)' if in_rec else 'cited PMID NOT found as [Ref.NNN] in record text; topical support only'
    if not in_rec and r['Sequence_ID']!='AMP_009038':
        extra=(extra or '') + ' DOWNGRADED to UNSURE: no cited PMID is embedded as [Ref.NNN] in the record (topical-only support).'
        res['Review_Result']='UNSURE_NEEDS_LITERATURE'
    res['Review_Reason']=f'[LIT-{NOW}] literature-verified ({chan}): {note}. {src}; journal/year existence and on-topic subject confirmed.{extra}'
    counts[res['Review_Result']]+=1
    out_rows.append(res)
with open('/home/user/rea/cloud-gpu/audits/2026-11-18-review-literature/positive_core_qc_review_LIT_pass_2026-11-18_NOT_SIGNED.tsv','w',newline='') as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys(), delimiter='\t')
    w.writeheader(); w.writerows(out_rows)
# audit table: per cited row
with open('/home/user/rea/cloud-gpu/audits/2026-11-18-review-literature/lit_pass_audit.tsv','w',newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['Sequence_ID','Evidence_Stratum','Citation_Candidates','PMID_status','Verdict','Proposed_Review_Result'])
    for r in out_rows:
        cand=(r['Citation_Candidates'] or '').strip()
        pmids=[c for c in re.split(r'[;,|/\s]+', cand) if c.strip().isdigit()]
        stat=';'.join(f'{p}:{"ok" if (p in PM and PM[p][1]) else ("flag" if p in PM else "missing")}' for p in pmids) or 'no-citation'
        w.writerow([r['Sequence_ID'], r['Evidence_Stratum'], cand or 'EMPTY', stat, r['Review_Result'] if False else ('on-topic' if all(p in PM and PM[p][1] for p in pmids) and pmids else ('flagged' if pmids else 'no-citation')), r['Review_Result']])
print(json.dumps(counts, ensure_ascii=False))
print('issues:', issues)
assert counts['CONFIRMED_CORE']+counts['UNSURE_NEEDS_LITERATURE']==230
