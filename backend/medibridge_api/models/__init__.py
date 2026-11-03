"""ORM models. Importing this package registers every table on ``Base.metadata``."""
from .audit import AuditLog
from .auth import AuthAttempt, AuthSession, DoctorProfile, Permission, Role, RolePermission, User
from .base import SCHEMAS, VOCAB, Base
from .clinical import (Allergy, CareTeamAssignment, ClinicalNote, ClinicalNoteVersion, Condition, ConsentRecord,
                       Encounter, Medication, MedicationStatement, Observation, Patient, PatientContact,
                       PatientIdentifier, Prescription, Procedure, Symptom, TranscriptionRecord, VitalSign)
from .documents import DocumentChunk, DocumentVersion, MedicalDocument
from .workflow import (AfterVisitSummary, AgentExecution, ApprovalRecord, DocumentationChangeSet, HandoffReport,
                       HandoffVersion, OutboxItem, WorkflowEvent, WorkflowRun)

__all__ = [
    "SCHEMAS", "VOCAB", "Base",
    "AuditLog",
    "AuthAttempt", "AuthSession", "DoctorProfile", "Permission", "Role", "RolePermission", "User",
    "Allergy", "CareTeamAssignment", "ClinicalNote", "ClinicalNoteVersion", "Condition", "ConsentRecord",
    "Encounter", "Medication", "MedicationStatement", "Observation", "Patient", "PatientContact", "PatientIdentifier",
    "Prescription", "Procedure", "Symptom", "TranscriptionRecord", "VitalSign",
    "DocumentChunk", "DocumentVersion", "MedicalDocument",
    "AfterVisitSummary", "AgentExecution", "ApprovalRecord", "DocumentationChangeSet", "HandoffReport", "HandoffVersion", "OutboxItem", "WorkflowEvent",
    "WorkflowRun",
]
