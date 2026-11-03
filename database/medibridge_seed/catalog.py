"""Fixed content for the synthetic cardiology dataset.

Patients come from Synthea (see synthea.py / cohort.py). This file holds only the staff, the facility name and
the import filters: which conditions, medications, results and procedures count as cardiology-relevant.
Medication-safety logic never reads this file; it uses only the sourced knowledge graph (knowledge/).
"""
from __future__ import annotations

import re

FACILITY = "MediBridge Heart Centre (fictional)"

STAFF = (
    # (role, given, family, specialty/department)
    ("doctor", "Imogen", "Castellane", "General Cardiology"),
    ("doctor", "Tobias", "Wrenfield", "Chest Pain Unit"),
    ("doctor", "Selin", "Arkwright", "Electrophysiology"),
    ("doctor", "Rohan", "Delacourt", "Heart Failure Clinic"),
    ("nurse", "Marisol", "Hartigan", "Cardiology Outpatients"),
    ("nurse", "Declan", "Ostrowe", "Chest Pain Unit"),
    ("nurse", "Anouk", "Vercelli", "Cardiac Intake"),
    ("admin", "Quentin", "Ashdown", "Clinical Systems"),
)

# ---------------------------------------------------------------- import filters (cardiology scope)

# Cardiac diagnoses and the cardiometabolic risk factors a cardiologist needs; social/dental/other findings are not
# imported ("cardiac data only").
CONDITION_KEEP = re.compile(
    r"heart|atrial fib|coronary|myocardial|ischemic|preinfarction|angina|hypertension|hyperlipid|hypertriglycerid|"
    r"diabetes mellitus type 2|prediabetes|chronic kidney disease|disorder of kidney due to diabetes|"
    r"microalbuminuria|proteinuria|obesity|aortic valve|mitral valve|stroke|anemia", re.I)
CONDITION_DROP = re.compile(r"retinopathy|macular|neuropathy|transplant|abnormal findings diagnostic imaging", re.I)

# LOINC code -> (display name, group). Values and units are imported exactly as Synthea recorded them.
LABS = {
    "18262-6": ("LDL cholesterol", "lipids"), "2093-3": ("Total cholesterol", "lipids"),
    "2085-9": ("HDL cholesterol", "lipids"), "2571-8": ("Triglycerides", "lipids"),
    "2823-3": ("Potassium", "renal"), "6298-4": ("Potassium", "renal"),
    "2951-2": ("Sodium", "renal"), "2947-0": ("Sodium", "renal"),
    "2160-0": ("Creatinine", "renal"), "38483-4": ("Creatinine", "renal"),
    "33914-3": ("eGFR", "renal"), "4548-4": ("HbA1c", "metabolic"), "718-7": ("Hemoglobin", "blood_count"),
    "6301-6": ("INR", "coagulation"), "5902-2": ("Prothrombin time", "coagulation"),
    "89579-7": ("High-sensitivity troponin I", "cardiac_markers"),
    "71425-3": ("NT-proBNP", "cardiac_markers"), "33762-6": ("NT-proBNP", "cardiac_markers"),
    "83107-3": ("NT-proBNP", "cardiac_markers"), "10230-1": ("LV ejection fraction", "echo"),
}
VITALS = {"8867-4": "Heart rate", "29463-7": "Body weight", "39156-5": "BMI", "8480-6": "Systolic blood pressure",
          "8462-4": "Diastolic blood pressure"}
LAB_GROUP_TITLES = {"lipids": "Lipid panel", "renal": "Renal function and electrolytes", "metabolic": "HbA1c",
                    "blood_count": "Hemoglobin", "coagulation": "Coagulation (INR)",
                    "cardiac_markers": "Cardiac biomarkers"}
# Typical adult reference intervals, printed on the synthetic lab PDFs for orientation only (assay-specific in reality).
REFERENCE = {"LDL cholesterol": (0, 100), "Total cholesterol": (0, 200), "HDL cholesterol": (40, 90),
             "Triglycerides": (0, 150), "Potassium": (3.5, 5.1), "Sodium": (135, 145), "Creatinine": (0.6, 1.3),
             "eGFR": (60, 120), "HbA1c": (4.0, 5.6), "Hemoglobin": (12.0, 17.5), "INR": (0.8, 1.2),
             "Prothrombin time": (11.0, 13.5), "High-sensitivity troponin I": (0, 26), "NT-proBNP": (0, 125),
             "LV ejection fraction": (52, 72)}

PROCEDURE_KEEP = re.compile(
    r"electrocardio|echocardio|angiography of coronary|percutaneous coronary|coronary artery bypass|cardiopulmonary "
    r"bypass|catheter ablation|cardioversion|aortic balloon|aortic valve|stress testing|cardiac status|swan-ganz|"
    r"insertion of catheter into artery|pacemaker", re.I)
MAJOR_PROCEDURES = re.compile(r"percutaneous coronary|coronary artery bypass|catheter ablation|aortic valve|"
                              r"cardioversion", re.I)

ENCOUNTER_CLASS = {"AMB": "ambulatory", "EMER": "emergency", "IMP": "inpatient", "VR": "virtual", "HH": "ambulatory"}
