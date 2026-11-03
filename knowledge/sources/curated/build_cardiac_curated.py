"""Writes cardiac_curated.json (next to this file). Every quote is copied from the fetched FDA labels.

    uv run python knowledge/sources/curated/build_cardiac_curated.py
    uv run python -m medibridge_knowledge.load      # verifies each quote against the labels, then loads
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "cardiac_curated.json"


def ev(label_of, section, quote):
    return {"label_of": label_of, "section": section, "quote": quote}


W_NSAID = ev("warfarin", "drug_interactions", "Non-steroidal Anti-Inflammatory Agents celecoxib, diclofenac, diflunisal, fenoprofen, ibuprofen")
W_NSAID2 = ev("warfarin", "drug_interactions", "ketorolac, mefenamic acid, naproxen")
W_BLEED = ev("warfarin", "drug_interactions", "Drugs that Can Increase the Risk of Bleeding")
W_APA = ev("warfarin", "drug_interactions", "Antiplatelet Agents aspirin, cilostazol, clopidogrel, dipyridamole, prasugrel")
W_CYP = ev("warfarin", "drug_interactions", "CYP2C9 amiodarone, capecitabine, cotrimoxazole, etravirine, fluconazole")
APIX = ev("apixaban", "drug_interactions", "Coadministration of antiplatelet agents, fibrinolytics, heparin, aspirin, and chronic NSAID use increases the risk of bleeding.")
RIVA = ev("rivaroxaban", "drug_interactions", "Coadministration of enoxaparin, warfarin, aspirin, clopidogrel and chronic NSAID use may increase the risk of bleeding")
SIMVA_CI = ev("simvastatin", "contraindications", "Concomitant use of strong CYP3A4 inhibitors (select azole anti-fungals, macrolide antibiotics, anti-viral medications, and nefazodone)")
SIMVA_LIST = ev("simvastatin", "drug_interactions", "select macrolide antibiotics (e.g., erythromycin and clarithromycin)")
SIMVA_AZOLE = ev("simvastatin", "drug_interactions", "Select azole anti-fungals (e.g., itraconazole, ketoconazole, posaconazole, and voriconazole)")
SIMVA_20 = ev("simvastatin", "drug_interactions", "For patients taking amiodarone, amlodipine, or ranolazine, do not exceed simvastatin 20 mg daily")
SIMVA_10 = ev("simvastatin", "drug_interactions", "For patients taking verapamil, diltiazem, or dronedarone, do not exceed simvastatin 10 mg daily.")
SIMVA_MYO = ev("simvastatin", "drug_interactions", "The risk of myopathy and rhabdomyolysis is increased by concomitant use of amiodarone, dronedarone, ranolazine, or calcium channel blockers with simvastatin.")
DIGOXIN = {
    "amiodarone": ev("digoxin", "drug_interactions", "Amiodarone 70% NA Measure serum digoxin concentrations before initiating concomitant drugs."),
    "verapamil": ev("digoxin", "drug_interactions", "Verapamil 50 to 75% NA"),
    "dronedarone": ev("digoxin", "drug_interactions", "Dronedarone NA 150%"),
    "clarithromycin": ev("digoxin", "drug_interactions", "Clarithromycin NA 70%"),
    "itraconazole": ev("digoxin", "drug_interactions", "Itraconazole 80% NA"),
    "erythromycin": ev("digoxin", "drug_interactions", "Erythromycin 100% NA"),
}
NTG_PDE5 = ev("nitroglycerin", "contraindications", "Use of phosphodiesterase type 5 (PDE-5) inhibitors, such as avanafil, sildenafil, tadalafil, or vardenafil")
SILD_NIT = ev("sildenafil", "contraindications", "Administration of sildenafil tablets to patients using nitric oxide donors, such as organic nitrates or organic nitrites in any form.")
TADA_NIT = ev("tadalafil", "contraindications", "Administration of tadalafil tablets to patients using any form of organic nitrate is contraindicated.")

interactions = []


def add(id_, drugs, severity, summary, evidence, **extra):
    interactions.append({"id": id_, "drugs": drugs, "severity": severity, "summary": summary, "evidence": evidence, **extra})


# --- bleeding risk with anticoagulants
for nsaid, quotes in (("ibuprofen", [W_BLEED, W_NSAID, ev("ibuprofen", "warnings", "take a blood thinning (anticoagulant) or steroid drug")]),
                      ("naproxen", [W_BLEED, W_NSAID2]), ("celecoxib", [W_BLEED, W_NSAID])):
    add(f"ddi-warfarin-{nsaid}", ["warfarin", nsaid], "major", "NSAIDs increase the risk of bleeding with warfarin.", quotes)
for apa in ("aspirin", "clopidogrel"):
    add(f"ddi-warfarin-{apa}", ["warfarin", apa], "major", "Antiplatelet agents increase the risk of bleeding with warfarin.", [W_BLEED, W_APA])
add("ddi-warfarin-prasugrel", ["warfarin", "prasugrel"], "major", "Prasugrel with warfarin increases the risk of bleeding.",
    [W_APA, ev("prasugrel", "drug_interactions", "Coadministration of prasugrel and warfarin increases the risk of bleeding")])
for inh in ("amiodarone", "fluconazole"):
    add(f"ddi-warfarin-{inh}", ["warfarin", inh], "major",
        f"{inh.capitalize()} inhibits CYP2C9 warfarin metabolism and can increase INR and bleeding risk.", [W_CYP])
for doac, quote in (("apixaban", APIX), ("rivaroxaban", RIVA)):
    for other in ("aspirin", "clopidogrel", "ibuprofen", "naproxen", "celecoxib"):
        add(f"ddi-{doac}-{other}", [doac, other], "major",
            f"{'NSAIDs' if other in ('ibuprofen', 'naproxen', 'celecoxib') else 'Antiplatelet agents'} increase the risk of bleeding with {doac}.",
            [quote])
for nsaid in ("ibuprofen", "naproxen", "celecoxib"):
    add(f"ddi-prasugrel-{nsaid}", ["prasugrel", nsaid], "moderate", "Chronic NSAID use with prasugrel may increase the risk of bleeding.",
        [ev("prasugrel", "drug_interactions", "Coadministration of prasugrel and NSAIDs (used chronically) may increase the risk of bleeding")])
add("ddi-clopidogrel-omeprazole", ["clopidogrel", "omeprazole"], "major", "Omeprazole reduces the antiplatelet effect of clopidogrel; avoid the combination.",
    [ev("clopidogrel", "drug_interactions", "Avoid concomitant use of clopidogrel with omeprazole or esomeprazole.")])

# --- statins
for inh, quote in (("clarithromycin", SIMVA_LIST), ("erythromycin", SIMVA_LIST), ("ketoconazole", SIMVA_AZOLE), ("itraconazole", SIMVA_AZOLE)):
    extra = [ev("clarithromycin", "contraindications", "Concomitant administration of clarithromycin tablets with HMG-CoA reductase inhibitors (statins) that are extensively metabolized by CYP3A4 (lovastatin or simvastatin) is contraindicated")] if inh == "clarithromycin" else []
    add(f"ddi-simvastatin-{inh}", ["simvastatin", inh], "contraindicated",
        f"Simvastatin labeling contraindicates strong CYP3A4 inhibitors such as {inh} (myopathy and rhabdomyolysis risk).",
        [SIMVA_CI, quote, *extra])
for other in ("amlodipine", "amiodarone", "ranolazine"):
    extra = [ev("amiodarone", "drug_interactions", "Limit the coadministered dose of simvastatin to 20 mg.")] if other == "amiodarone" else []
    add(f"ddi-simvastatin-{other}", ["simvastatin", other], "major",
        f"With {other}, simvastatin should not exceed 20 mg daily (myopathy risk).", [SIMVA_20, *extra],
        dose_limit={"drug": "simvastatin", "max_daily_mg": 20})
for other in ("verapamil", "diltiazem", "dronedarone"):
    add(f"ddi-simvastatin-{other}", ["simvastatin", other], "major",
        f"With {other}, simvastatin should not exceed 10 mg daily (myopathy risk).", [SIMVA_10, SIMVA_MYO],
        dose_limit={"drug": "simvastatin", "max_daily_mg": 10})

# --- digoxin
for other, quote in DIGOXIN.items():
    extra = [ev("digoxin", "drug_interactions", "Sudden death was more common in patients receiving digoxin with dronedarone than on either alone")] if other == "dronedarone" else []
    add(f"ddi-digoxin-{other}", ["digoxin", other], "major",
        f"{other.capitalize()} increases digoxin concentrations; measure digoxin levels (toxicity risk).", [quote, *extra])

# --- nitrates and PDE-5 inhibitors
add("ddi-nitroglycerin-sildenafil", ["nitroglycerin", "sildenafil"], "contraindicated",
    "Nitrates with PDE-5 inhibitors can cause severe hypotension; the combination is contraindicated.", [NTG_PDE5, SILD_NIT])
add("ddi-nitroglycerin-tadalafil", ["nitroglycerin", "tadalafil"], "contraindicated",
    "Nitrates with PDE-5 inhibitors can cause severe hypotension; the combination is contraindicated.", [NTG_PDE5, TADA_NIT])
add("ddi-isosorbide-sildenafil", ["isosorbide mononitrate", "sildenafil"], "contraindicated",
    "Organic nitrates with PDE-5 inhibitors can cause severe hypotension; the combination is contraindicated.", [SILD_NIT])
add("ddi-isosorbide-tadalafil", ["isosorbide mononitrate", "tadalafil"], "contraindicated",
    "Organic nitrates with PDE-5 inhibitors can cause severe hypotension; the combination is contraindicated.", [TADA_NIT])

# --- potassium
add("ddi-spironolactone-potassium", ["spironolactone", "potassium chloride"], "major",
    "Spironolactone with potassium supplements may lead to severe hyperkalemia.",
    [ev("spironolactone", "drug_interactions", "Concomitant administration of spironolactone tablets with potassium supplementation or drugs that can increase potassium may lead to severe hyperkalemia.")])
for raas in ("lisinopril", "enalapril", "ramipril", "losartan", "valsartan"):
    quotes = [ev("spironolactone", "warnings_and_cautions", "drugs that increase potassium, such as angiotensin converting enzyme inhibitors and angiotensin receptor blockers")]
    if raas == "lisinopril":
        quotes.append(ev("lisinopril", "drug_interactions", "Potassium-sparing diuretics (spironolactone, amiloride, triamterene, and others) can increase the risk of hyperkalemia."))
    add(f"ddi-spironolactone-{raas}", ["spironolactone", raas], "moderate",
        f"Spironolactone with {raas} increases the risk of hyperkalemia; monitor potassium.", quotes)
add("ddi-eplerenone-potassium", ["eplerenone", "potassium chloride"], "contraindicated",
    "Eplerenone is contraindicated with potassium supplements.",
    [ev("eplerenone", "contraindications", "Concomitant use of potassium supplements or potassium-sparing diuretics")])
for inh in ("clarithromycin", "ketoconazole", "itraconazole"):
    add(f"ddi-eplerenone-{inh}", ["eplerenone", inh], "contraindicated", f"Eplerenone is contraindicated with strong CYP3A inhibitors such as {inh}.",
        [ev("eplerenone", "contraindications", "concomitant administration of strong CYP3A inhibitors (e.g., ketoconazole, itraconazole, nefazodone, troleandomycin, clarithromycin")])

# --- antiarrhythmics and rate control
for other, quote in (("verapamil", "Concomitant use of verapamil is contraindicated."),
                     ("hydrochlorothiazide", "Concomitant use of HCTZ alone or in combination with triamterene is contraindicated.")):
    add(f"ddi-dofetilide-{other}", ["dofetilide", other], "contraindicated",
        f"{other.capitalize()} increases dofetilide concentrations and QT prolongation; contraindicated.", [ev("dofetilide", "drug_interactions", quote)])
add("ddi-dofetilide-ketoconazole", ["dofetilide", "ketoconazole"], "contraindicated", "Ketoconazole with dofetilide is contraindicated.",
    [ev("dofetilide", "contraindications", "trimethoprim (alone or in combination with sulfamethoxazole), or ketoconazole with dofetilide capsules is contraindicated")])
for inh in ("ketoconazole", "itraconazole", "clarithromycin"):
    add(f"ddi-dronedarone-{inh}", ["dronedarone", inh], "contraindicated", f"Potent CYP3A inhibitors such as {inh} are contraindicated with dronedarone.",
        [ev("dronedarone", "drug_interactions", "Concomitant use of ketoconazole as well as other potent CYP3A inhibitors such as itraconazole, voriconazole, ritonavir, clarithromycin, and nefazodone is contraindicated")])
for inh in ("clarithromycin", "itraconazole", "ketoconazole"):
    add(f"ddi-ivabradine-{inh}", ["ivabradine", inh], "contraindicated", f"Strong CYP3A4 inhibitors such as {inh} are contraindicated with ivabradine.",
        [ev("ivabradine", "drug_interactions", "The concomitant use of strong CYP3A4 inhibitors is contraindicated")])
for ccb in ("verapamil", "diltiazem"):
    add(f"ddi-ivabradine-{ccb}", ["ivabradine", ccb], "major", f"{ccb.capitalize()} increases ivabradine exposure and lowers heart rate; avoid.",
        [ev("ivabradine", "warnings_and_cautions", "Concurrent use of verapamil or diltiazem will increase ivabradine exposure, may themselves contribute to heart rate lowering, and should be avoided")])

# --- heart failure therapy
for acei in ("lisinopril", "enalapril", "ramipril"):
    quotes = [ev("sacubitril", "contraindications", "Do not administer within 36 hours of switching from or to an ACE inhibitor")]
    if acei == "enalapril":
        quotes.append(ev("enalapril", "contraindications", "Do not administer enalapril maleate within 36 hours of switching to or from sacubitril/valsartan"))
    add(f"ddi-sacubitril-{acei}", ["sacubitril", acei], "contraindicated",
        "Sacubitril/valsartan with an ACE inhibitor is contraindicated (angioedema); allow 36 hours between them.", quotes)

condition_cautions = [
    {"id": "cc-nsaid-heart-failure", "drugs": ["naproxen", "celecoxib", "ibuprofen"], "condition_terms": ["heart failure"],
     "severity": "major", "summary": "NSAIDs can worsen heart failure (fluid retention); avoid in severe heart failure.",
     "evidence": [ev("naproxen", "warnings_and_cautions", "Avoid use of naproxen tablets and naproxen sodium tablets in patients with severe heart failure unless benefits are expected to outweigh risk of worsening heart failure."),
                  ev("celecoxib", "warnings_and_cautions", "Avoid use of celecoxib capsules in patients with severe heart failure unless benefits are expected to outweigh risk of worsening heart failure"),
                  ev("ibuprofen", "warnings", "NSAIDs, except aspirin, increase the risk of heart attack, heart failure, and stroke.")]},
    {"id": "cc-dronedarone-heart-failure", "drugs": ["dronedarone"], "condition_terms": ["heart failure"],
     "severity": "major", "summary": "Dronedarone is contraindicated in symptomatic heart failure with recent decompensation or NYHA class IV.",
     "evidence": [ev("dronedarone", "boxed_warning", "MULTAQ is contraindicated in patients with symptomatic heart failure with recent decompensation requiring hospitalization or NYHA Class IV heart failure")]},
]

allergy_substances = [
    {"id": "allergen-ace-inhibitor", "terms": ["lisinopril", "enalapril", "ramipril", "ace inhibitor", "ace inhibitors",
                                              "angiotensin converting enzyme inhibitor"],
     "maps_to": [{"kind": "epc", "name": "Angiotensin Converting Enzyme Inhibitor", "relation": "CONTRAINDICATED_FOR",
                  "basis": "FDA EPC class membership"}],
     "cautions": [{"kind": "ingredient", "name": "sacubitril", "basis": "label statement",
                   "evidence": ev("sacubitril", "contraindications", "in patients with a history of angioedema related to previous ACE inhibitor or ARB therapy")}],
     "evidence": ev("lisinopril", "contraindications", "a history of angioedema or hypersensitivity related to previous treatment with an angiotensin converting enzyme inhibitor")},
    {"id": "allergen-aspirin", "terms": ["aspirin", "asa", "acetylsalicylic acid"],
     "maps_to": [{"kind": "ingredient", "name": "aspirin", "relation": "CONTRAINDICATED_FOR", "basis": "same ingredient"}],
     "cautions": [{"kind": "epc", "name": "Nonsteroidal Anti-inflammatory Drug", "basis": "label statement",
                   "evidence": ev("ibuprofen", "warnings", "if you have ever had an allergic reaction to any other pain reliever/fever reducer")}],
     "evidence": ev("aspirin", "warnings", "Aspirin may cause severe allergic reactions which may include:")},
    {"id": "allergen-nsaid", "terms": ["ibuprofen", "naproxen", "celecoxib", "nsaid", "nsaids", "advil", "motrin", "aleve"],
     "maps_to": [{"kind": "epc", "name": "Nonsteroidal Anti-inflammatory Drug", "relation": "CONTRAINDICATED_FOR",
                  "basis": "FDA EPC class membership"}],
     "evidence": ev("ibuprofen", "warnings", "if you have ever had an allergic reaction to any other pain reliever/fever reducer")},
]

condition_aliases = {
    "_note": ("Patient condition names (lower case) -> MED-RT disease names. MED-RT 'ci_with' links are coarse (e.g. "
              "heart failure is listed for beta-blockers that treat it), so every MED-RT match is reported as a "
              "'caution - check the label for the specific circumstance', never as a blocking contraindication. "
              "Only label-quoted curated edges can block."),
    "chronic congestive heart failure": [["Heart Failure", "exact"]],
    "heart failure": [["Heart Failure", "exact"]],
    "atrial fibrillation": [["Atrial Fibrillation", "exact"]],
    "ischemic heart disease": [["Coronary Disease", "review"]],
    "coronary arteriosclerosis": [["Coronary Artery Disease", "exact"]],
    "acute non-st segment elevation myocardial infarction": [["Myocardial Infarction", "exact"]],
    "acute st segment elevation myocardial infarction": [["Myocardial Infarction", "exact"]],
    "myocardial infarction": [["Myocardial Infarction", "exact"]],
    "history of myocardial infarction": [["Myocardial Infarction", "review"]],
    "diabetes mellitus type 2": [["Diabetes Mellitus, Type 2", "exact"]],
    "chronic kidney disease stage 3": [["Renal Insufficiency", "review"]],
    "chronic kidney disease stage 4": [["Renal Insufficiency", "review"]],
    "disorder of kidney due to diabetes mellitus": [["Kidney Diseases", "review"]],
    "anemia": [["Anemia", "exact"]],
}

OUT.write_text(json.dumps({
    "_status": ("DEMO-CURATED, NOT CLINICALLY REVIEWED. Cardiology scope. Each edge restates a statement found verbatim in "
                "the cited FDA label section (openFDA SPL); the loader rejects any edge whose quote is not present in the "
                "fetched label text. Illustrative, not a comprehensive interaction database."),
    "curated_by": "MediBridge AI build (synthetic research demo)",
    "scope": "cardiology",
    "interactions": interactions, "condition_cautions": condition_cautions,
    "allergy_substances": allergy_substances, "condition_aliases": condition_aliases,
}, indent=1), encoding="utf-8")
print(len(interactions), "interactions,", len(condition_cautions), "condition cautions,", len(allergy_substances), "allergy maps")
