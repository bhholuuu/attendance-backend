# Student Bulk Import Guide

This guide explains how to add many students at once using the CSV import
feature added for full-school deployment. Only an **ADMIN** account can run an
import. A ready-to-copy prefilled example lives in
`backend/STUDENT_IMPORT_TEMPLATE.csv` (all example rows are **fake data**).

> Small additions (a few students) are easier done one-by-one from the Students
> screen. Imports are for the initial school rollout and bulk corrections.

---

## 1. What the CSV must look like

A single header row (exact text), followed by one student per row:

```
class_name,division_name,student_name,roll_number,parent_name,parent_whatsapp_number
10,A,Fake Student One,1,Fake Parent One,+919876543201
```

| Column | Required | Rules |
| --- | --- | --- |
| `class_name` | yes | Must exactly match an **active** class, e.g. `10`. |
| `division_name` | yes | Must exactly match an **active** division in that class, e.g. `A`. |
| `student_name` | yes | Up to 100 characters. |
| `roll_number` | yes | Up to 30 characters. Unique among **active** students in the same class + division. |
| `parent_name` | yes | Up to 100 characters. |
| `parent_whatsapp_number` | yes | A valid WhatsApp number, e.g. `+919876543210`. Optional `.` `-` spaces `( )` are stripped. |

Notes:

- Keep the **first row exactly** as shown. Extra spaces around the header
  values are allowed, but the column names must match.
- UTF-8 file encoding (recommended). A UTF-8 **BOM** is accepted.
- Maximum file size **2 MB**, maximum **5,000 rows** per file.
- Multiple students may share the same parent phone number — that is allowed
  (e.g. siblings).
- A roll number that already exists for an **active** student in the same
  class + division is a conflict and will **not** be overwritten.
- Only **active** classes/divisions can be used.

## 2. Flow

1. **Prepare** the CSV (see template, fields above).
2. **Preview** — upload the file. The system validates every row and returns:
   valid/invalid counts and a line-by-line error list.
   - Nothing is written during preview.
   - The response includes a `preview_token`.
3. **Commit** — confirm by resubmitting the **same file** with its
   `preview_token`.
   - Default (`allow_partial = false`): if **any** row is invalid the whole
     import is refused — nothing is imported.
   - Optional (`allow_partial = true`): only the valid rows are imported, in a
     single transaction; every invalid row is reported and skipped.

## 3. Row numbering in error messages

Row numbers are 1-based and **include the header**:

- Header = row 1.
- First data row = row 2.

So `"row_number": 5` points at the 4th student in the file.

## 4. Typical errors and fixes

| Error | Meaning / fix |
| --- | --- |
| `Class not found or inactive.` | Fix `class_name`; create/activate the class first. |
| `Division not found in this class or inactive.` | Fix `division_name`; create/activate it inside that class first. |
| `Roll number already in use by an active student in this class & division.` | Change the roll, or deactivate the old student first. |
| `Duplicate roll number within the file.` | Remove or renumber the duplicate row. |
| Invalid phone message | Fix `parent_whatsapp_number` (format `+<country code><number>`). |
| Structural errors (`bad header`, `file is empty`, `too many records`) | Correct the file and try again. |

If a commit is refused, correct the reported rows and run the import again.
Nothing is partially written unless `allow_partial = true`.

## 5. Safe to re-run?

Yes. An import only **adds** students; it never updates or deletes existing
ones. Re-running after fixing the reported rows is safe. Each successful
commit also writes an **import audit record** (file name, row counts, who ran
it, when) so the rollout is traceable. Raw file contents are never stored.