import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from dean_agent.models import AuditLog, ChangeProposal, Group, Student, StudentGroupHistory


class ProposalError(ValueError):
    pass


def propose_group_change(
    db: Session, *, student_id: int, new_group_code: str, actor_id: str, agent_run_id: str
) -> ChangeProposal:
    student = db.get(Student, student_id)
    group = db.scalar(select(Group).where(Group.code == new_group_code))
    if student is None or group is None:
        raise ProposalError("Студент или новая группа не найдены")
    if student.group_id == group.id:
        raise ProposalError("Студент уже находится в этой группе")
    proposal = ChangeProposal(
        student_id=student.id,
        actor_id=actor_id,
        agent_run_id=agent_run_id,
        field="group_id",
        old_value={"group_id": student.group_id, "group_code": student.group.code},
        new_value={"group_id": group.id, "group_code": group.code},
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    return proposal


def resolve_proposal(db: Session, proposal_id: uuid.UUID, actor_id: str, approve: bool) -> ChangeProposal:
    # Lock proposal and student in a fixed order to prevent competing confirmations.
    proposal = db.scalar(select(ChangeProposal).where(ChangeProposal.id == proposal_id).with_for_update())
    if proposal is None:
        raise ProposalError("Предложение не найдено")
    if proposal.status != "pending":
        raise ProposalError("Предложение уже обработано")
    student = db.scalar(select(Student).where(Student.id == proposal.student_id).with_for_update())
    if student is None:
        raise ProposalError("Студент не найден")
    if approve and student.group_id != proposal.old_value["group_id"]:
        raise ProposalError("Группа студента изменилась; создайте новое предложение")
    if approve and db.get(Group, proposal.new_value["group_id"]) is None:
        raise ProposalError("Новая группа больше не существует")
    proposal.status = "approved" if approve else "rejected"
    proposal.resolved_at = datetime.now(timezone.utc)
    if approve:
        student.group_id = proposal.new_value["group_id"]
        db.add(StudentGroupHistory(
            student_id=student.id,
            old_group_id=proposal.old_value["group_id"],
            new_group_id=student.group_id,
            actor_id=actor_id,
        ))
    db.add(AuditLog(
        actor_id=actor_id,
        agent_run_id=proposal.agent_run_id,
        action="approve_group_change" if approve else "reject_group_change",
        entity_type="student",
        entity_id=str(student.id),
        old_value=proposal.old_value,
        new_value=proposal.new_value if approve else proposal.old_value,
    ))
    db.commit()
    db.refresh(proposal)
    return proposal
