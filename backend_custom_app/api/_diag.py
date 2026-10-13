import frappe
from education.education.doctype.student_group.student_group import fetch_students, get_program_enrollment


def execute():
    groups = frappe.get_all(
        "Student Group",
        filters={"disabled": 0},
        fields=["name", "academic_year", "academic_term", "program", "batch",
                "course", "group_based_on", "student_category"],
    )
    for g in groups:
        filters = {"group_based_on": g.group_based_on}
        if g.group_based_on != "Activity":
            filters.update({
                "academic_year": g.academic_year,
                "academic_term": g.academic_term,
                "program": g.program,
                "batch": g.batch,
                "student_category": g.student_category,
                "course": g.course,
                "student_group": g.name,
            })
        rows = fetch_students("Student", "", "name", 0, 50, filters)
        names = [r[0] for r in rows]
        m = "MAHESH" if "EDU-STU-2026-00027" in names else "-"
        s = "SOURABH" if "EDU-STU-2026-00026" in names else "-"
        print(f"\nGROUP: {g.name}")
        print(f"  term={g.academic_term} batch={g.batch} course={g.course} cat={g.student_category} based={g.group_based_on}")
        print(f"  eligible={names}")
        print(f"  >>> mahesh={m} sourabh={s}")

    print("\n=== Program Enrollment COURSES child rows ===")
    for pe in ("EDU-ENR-2026-00019", "EDU-ENR-2026-00020"):
        n = frappe.db.count("Program Enrollment Course", {"parent": pe})
        print(f"  {pe}: {n} course rows")

    print("\n=== Batch doctype values ===")
    print(frappe.get_all("Student Batch Name", pluck="name"))
