# run_validation.py
from app.lpp import LPPValidator
import json

def main():
    validator = LPPValidator()

    loan_id = "HML-450321"
    print(f"Running validation for loan ID: {loan_id}")

    try:
        # validate_loan now returns FINAL UI OBJECT
        result = validator.validate_loan(loan_id)

        print("\nFINAL RESULT (Saved to DB & UI Ready)")
        print("-----------------------------------")
        print(json.dumps(result, indent=2))

        # Print summary
        summary = result.get("loan_details", {}).get("action_summary", {})

        print("\nValidation Summary:")
        print("------------------")
        print(f"Total Checks: {summary.get('total_checks', 0)}")
        print(f"Alerts: {summary.get('alerts_count', 0)}")
        print(f"Updates: {summary.get('updates_count', 0)}")
        print(f"Conditions: {summary.get('conditions_count', 0)}")

    except Exception as e:
        print(f"\nError during validation: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
