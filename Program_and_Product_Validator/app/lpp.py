#lpp.py
import os
import yaml
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import copy
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv
import logging
from dateutil import parser
import difflib

# -------------------------------------------------
# ENV
# -------------------------------------------------
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "3NP_Mortgage_AI")
LOS_COLLECTION = os.getenv("LOS_COLLECTION", "LOS_Data")
INDEXER_COLLECTION = os.getenv("INDEXER_COLLECTION", "Document_Indexer_DEV")
RESULTS_COLLECTION = os.getenv("RESULTS_COLLECTION", "Disclosure_PPV")

BASE_DIR = os.path.dirname(__file__)
CONFIG_DIR = os.path.join(BASE_DIR, "config")
FIELDS_CONFIG_PATH = os.path.join(CONFIG_DIR, "fields.yaml")
RULES_CONFIG_PATH = os.path.join(CONFIG_DIR, "rules.yaml")
PPV_CONFIG_PATH = os.path.join(CONFIG_DIR, "ppv.yaml")

# -------------------------------------------------
# LOGGER
# -------------------------------------------------
def get_logger(name: str = __name__) -> logging.Logger:
    lvl = os.getenv('LOG_LEVEL', 'INFO').upper()
    logger = logging.getLogger(name)
    if not logger.handlers:
        ch = logging.StreamHandler()
        fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        ch.setFormatter(fmt)
        logger.addHandler(ch)
    logger.setLevel(getattr(logging, lvl, logging.INFO))
    return logger

logger = get_logger("app.lpp")

# -------------------------------------------------
# JSON UTILS
# -------------------------------------------------
def clean_mongo(obj):
    """
    Recursively convert MongoDB-native types into JSON-serializable primitives.
    ObjectId -> str, datetime -> isoformat
    """
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[k] = clean_mongo(v)
        return out
    if isinstance(obj, list):
        return [clean_mongo(x) for x in obj]
    try:
        return str(obj)
    except:
        return obj

# -------------------------------------------------
# FUZZY MATCHER UTILS
# -------------------------------------------------
try:
    from rapidfuzz import fuzz
    def fuzzy_ratio(a, b):
        if a is None or b is None:
            return 0
        return int(fuzz.token_set_ratio(str(a), str(b)))
except Exception:
    def fuzzy_ratio(a, b):
        if a is None or b is None:
            return 0
        return int(difflib.SequenceMatcher(None, str(a), str(b)).ratio() * 100)

def normalize_string(s):
    if s is None:
        return ""
    s = str(s)
    return ''.join(ch.lower() for ch in s if ch.isalnum() or ch.isspace()).strip()

# -------------------------------------------------
# DATE UTILS
# -------------------------------------------------
SUPPORTED_FORMATS = [
    "%m-%d-%Y",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
]

def parse_date(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    value = str(value).strip()
    for fmt in SUPPORTED_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except:
            pass
    try:
        return parser.parse(value)
    except:
        return None

def months_between(d1, d2=None) -> int:
    d1 = parse_date(d1)
    if d1 is None:
        raise ValueError(f"Invalid date: {d1}")
    d2 = datetime.now() if d2 is None else parse_date(d2)
    if d2 is None:
        raise ValueError(f"Invalid date: {d2}")
    return abs((d2.year - d1.year) * 12 + (d2.month - d1.month))

def days_between(d1, d2=None) -> int:
    d1 = parse_date(d1)
    if d1 is None:
        raise ValueError("Invalid d1")
    d2 = datetime.now() if d2 is None else parse_date(d2)
    if d2 is None:
        raise ValueError("Invalid d2")
    return abs((d2 - d1).days)

# -------------------------------------------------
# MONGO WRAPPER
# -------------------------------------------------
class MongoClientWrapper:
    def __init__(self):
        self.client = MongoClient(MONGO_URI)
        self.db = self.client[MONGO_DB]

    def _sanitize(self, obj):
        return clean_mongo(obj)

    def get_los_by_loanid(self, loan_id: str) -> Optional[Dict[str, Any]]:
        resolver = PathResolver()
        coll = self.db[LOS_COLLECTION]

        logger.info(f"Searching LOS for loan_id={loan_id}")

        for doc in coll.find({}):
            ctx = {"los": doc}
            resolved_id = resolver.resolve(ctx, "los", "loan_id")
            if resolved_id and str(resolved_id).strip() == str(loan_id).strip():
                logger.info("LOS document found")
                return self._sanitize(doc)

        logger.error(f"Loan ID {loan_id} NOT FOUND in LOS")
        return None

    def save_disclosure_ppv(self, payload: Dict[str, Any]) -> str:
        loan_id = payload.get("loan_id")
        if loan_id:
            self.db[RESULTS_COLLECTION].delete_many({"loan_id": loan_id})
        to_insert = copy.deepcopy(payload)
        to_insert["created_at"] = datetime.now(timezone.utc)
        result = self.db[RESULTS_COLLECTION].insert_one(to_insert)
        logger.info(f"Saved Disclosure_PPV with ID: {result.inserted_id}")
        return str(result.inserted_id)

# -------------------------------------------------
# PATH RESOLVER (DOT-WISE)
# -------------------------------------------------
class PathResolver:
    def __init__(self):
        with open(PPV_CONFIG_PATH, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
        self.fields = self.cfg["fields"]

    def resolve(self, context: Dict[str, Any], section: str, name: str):
        cfg = self.fields.get(section, {}).get(name)
        if not cfg:
            return None
        path = cfg.get("path")
        default = cfg.get("default")
        cur = context.get(section)
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur if cur is not None else default
# -------------------------------------------------
# BASE VALIDATOR
# -------------------------------------------------
class BaseValidator:
    def pass_result(self, rule: Dict[str, Any], details=None):
        return {"rule_id": rule.get("id"), "status": "PASS", "message": "", "details": details or {}}
    
    def alert_result(self, rule: Dict[str, Any], message=None, details=None):
        return {"rule_id": rule.get("id"), "status": "ALERT", "message": message or rule.get("alert_message",""), "details": details or {}}
    
    def condition_result(self, rule: Dict[str, Any], message=None, details=None):
        return {"rule_id": rule.get("id"), "status": "CONDITION", "message": message or rule.get("condition_message",""), "details": details or {}}
    
    def not_applicable_result(self, rule: Dict[str, Any], details=None):
        return {"rule_id": rule.get("id"), "status": "NOT_APPLICABLE", "message": "", "details": details or {}}

# -------------------------------------------------
# ALL VALIDATORS
# -------------------------------------------------
class LTVValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        ltv = resolver.resolve(context, 'los', 'ltv')
        cltv = resolver.resolve(context, 'los', 'cltv')
        hcltv = resolver.resolve(context, 'los', 'hcltv')
        thresholds = rule.get('thresholds', {})
        details = {'ltv': ltv, 'cltv': cltv, 'hcltv': hcltv, 'thresholds': thresholds}
        def to_pct(x):
            if x is None:
                return None
            try:
                v = float(x)
                if v <= 1:
                    v = v * 100
                return v
            except:
                return None
        l = to_pct(ltv)
        c = to_pct(cltv)
        h = to_pct(hcltv)
        if thresholds:
            if l is not None and thresholds.get('ltv') is not None and l > thresholds.get('ltv'):
                return self.alert_result(rule, details=details)
            if c is not None and thresholds.get('cltv') is not None and c > thresholds.get('cltv'):
                return self.alert_result(rule, details=details)
            if h is not None and thresholds.get('hcltv') is not None and h > thresholds.get('hcltv'):
                return self.alert_result(rule, details=details)
        return self.pass_result(rule, details=details)

class DTIValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        dti = resolver.resolve(context, 'los', 'dti')
        params = rule.get('params', {})
        limit = params.get('dti_limit', 50)
        details = {'dti': dti, 'limit': limit}
        try:
            d = float(dti)
        except:
            return self.not_applicable_result(rule)
        if d > limit:
            return self.alert_result(rule, details=details)
        return self.pass_result(rule, details=details)

class OccupancyValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        prop = resolver.resolve(context, 'los', 'property_will_be')
        details = {'property_will_be': prop}
        if prop is None or prop == "":
            return self.not_applicable_result(rule)
        if str(prop).strip().lower() != 'primary':
            return self.alert_result(rule, details=details)
        return self.pass_result(rule, details=details)

class SecondHomeValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        no_units = resolver.resolve(context, 'los', 'no_units')
        details = {'no_units': no_units}
        try:
            if int(no_units) != 1:
                return self.alert_result(rule, details=details)
        except:
            return self.not_applicable_result(rule)
        return self.pass_result(rule, details=details)

class InvestmentValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        loan_prog = resolver.resolve(context, 'los', 'loan_program_detail')
        prop_type = resolver.resolve(context, 'los', 'property_type')
        details = {'loan_program_detail': loan_prog, 'property_type': prop_type}
        if loan_prog and 'manufactured' in str(loan_prog).lower():
            return self.alert_result(rule, details=details)
        if prop_type and 'manufactured' in str(prop_type).lower():
            return self.alert_result(rule, details=details)
        return self.pass_result(rule, details=details)

class CreditScoreValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        score = resolver.resolve(context, 'los', 'average_representative_credit_score')
        details = {'score': score}
        try:
            s = float(score)
        except:
            return self.not_applicable_result(rule)
        if s <= 620:
            return self.alert_result(rule, details=details)
        return self.pass_result(rule, details=details)

class GiftValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        gift_amount = resolver.resolve(context, 'los', 'gift_amount')
        cash_to = resolver.resolve(context, 'los', 'cash_to_borrower')
        details = {'gift_amount': gift_amount}

        loan_program_detail_ = resolver.resolve(context,'los' ,'loan_program_detail')
        try:
            g = float(gift_amount) if gift_amount is not None else 0.0
        except:
            g = 0.0
       
        if g > 0 :
            return self.alert_result(rule, message=rule.get('alert_message'), details=details)
        return self.pass_result(rule, details=details)

class CashoutSeasoningValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        liabilities_acc = resolver.resolve(context, 'los', 'liabilities_account_number') or ""
        liabilities_name = resolver.resolve(context, 'los', 'liabilities_name') or ""
        est_close = resolver.resolve(context, 'los', 'estimated_closing_date')
        details = {
            'liabilities_account_number': liabilities_acc,
            'liabilities_name': liabilities_name,
            'estimated_closing_date': est_close
        }

        tradelines_raw = context.get("credit_report", {}).get("Tradelines")

        if not tradelines_raw:
            return self.not_applicable_result(rule)

        if isinstance(tradelines_raw, dict):
            tradelines = [tradelines_raw]
        else:
            tradelines = tradelines_raw

        last4 = str(liabilities_acc)[-4:] if liabilities_acc else ""
        matched = None

        for t in tradelines:
            acc_no = t.get("Creditor Account Number") or t.get("Creditor_Account_Number") or ""
            acc_last4 = str(acc_no)[-4:] if acc_no else ""
            creditor_name = t.get("Creditor Name") or t.get("Creditor_Name") or ""

            if last4 and acc_last4 and last4 == acc_last4:
                score = fuzzy_ratio(creditor_name, liabilities_name)
                details.update({
                    'matched_account_last4': acc_last4,
                    'creditor_name': creditor_name,
                    'fuzzy_score': score
                })
                if score >= 70:
                    matched = t
                    break

        if not matched:
            return self.alert_result(
                rule,
                message="The mortgage being paid off is not reported on credit report  please review reasoning requirement.",
                details=details
            )

        date_opened = matched.get("Date_Opened")

        if not date_opened or not est_close:
            return self.not_applicable_result(rule)

        d_open = parse_date(date_opened)
        d_close = parse_date(est_close)

        if not d_open or not d_close:
            return self.not_applicable_result(rule)

        months = months_between(d_open, d_close)

        if months <= 12:
            return self.alert_result(
                rule,
                message="The seasoning requirement for cash out refinance is not met, review and proceed",
                details=details
            )

        return self.pass_result(rule, details=details)

class TitleValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        chain_date = resolver.resolve(context, 'title', 'chain_title_date')
        est_close = resolver.resolve(context, 'los', 'estimated_closing_date')
        details = {'chain_title_date': chain_date, 'estimated_closing_date': est_close}
        if not chain_date or not est_close:
            return self.not_applicable_result(rule)
        try:
            months = months_between(parse_date(chain_date), parse_date(est_close))
        except:
            return self.not_applicable_result(rule)
        if months < rule.get('params', {}).get('min_months', 6):
            return self.alert_result(rule, details=details)
        return self.pass_result(rule, details=details)

class FraudValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        drive = context.get('drive_report', {})
        est_close = resolver.resolve(context, 'los', 'estimated_closing_date')
        dr_street = resolver.resolve(context, 'drive_report', 'drive_street')
        dr_city = resolver.resolve(context, 'drive_report', 'drive_city')
        dr_state = resolver.resolve(context, 'drive_report', 'drive_state')
        dr_unit = resolver.resolve(context, 'drive_report', 'drive_unit')
       
        subj_street = resolver.resolve(context, 'los', 'urla_lender_subject_street')
        subj_city = resolver.resolve(context, 'los', 'urla_lender_subject_city')
        subj_state = resolver.resolve(context, 'los', 'urla_lender_subject_state')
        subj_unit = resolver.resolve(context, 'los', 'urla_lender_subject_unit')
        details = {'drive_addr': {'street': dr_street, 'city': dr_city, 'state': dr_state, 'unit': dr_unit},
                   'subject_addr': {'street': subj_street, 'city': subj_city, 'state': subj_state, 'unit': subj_unit}}
        print(dr_street)
        print(subj_street)
        if normalize_string(dr_street) != normalize_string(subj_street) or \
           normalize_string(dr_city) != normalize_string(subj_city) or \
           normalize_string(dr_state) != normalize_string(subj_state) or \
           normalize_string(dr_unit) != normalize_string(subj_unit):
            return self.not_applicable_result(rule)
        fraud_date = resolver.resolve(context, 'drive_report', 'fraud_recorded_date')
        if not fraud_date or not est_close:
            return self.not_applicable_result(rule)
        try:
            months = months_between(parse_date(fraud_date), parse_date(est_close))
        except:
            return self.not_applicable_result(rule)
        if months < rule.get('params', {}).get('max_months', 6):
            return self.alert_result(rule, details=details)
        return self.pass_result(rule, details=details)

class AppraisalPriorSaleValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        prior_sale = resolver.resolve(context, 'appraisal', 'prior_sale_date')
        est_close = resolver.resolve(context, 'los', 'estimated_closing_date')
        details = {'prior_sale_date': prior_sale, 'estimated_closing_date': est_close}
        if not prior_sale or not est_close:
            return self.not_applicable_result(rule)
        try:
            months = months_between(parse_date(prior_sale), parse_date(est_close))
        except:
            return self.not_applicable_result(rule)
        if months < rule.get('params', {}).get('min_months', 6):
            return self.alert_result(rule, details=details)
        return self.pass_result(rule, details=details)

class LoanProgramValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        amort = resolver.resolve(context, 'los', 'amortization_type')
        details = {'amortization_type': amort}
        if not amort:
            return self.not_applicable_result(rule)
        if str(amort).strip().lower() not in ['fixed rate', 'fixed']:
            return self.alert_result(rule, details=details)
        return self.pass_result(rule, details=details)

class CashbackValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        cash_from = resolver.resolve(context, 'los', 'cash_from_borrower')
        cash_to = resolver.resolve(context, 'los', 'cash_to_borrower')
        loan_amount = resolver.resolve(context, 'los', 'loan_amount')
        details = {'cash_from': cash_from, 'cash_to': cash_to, 'loan_amount': loan_amount}
        try:
            loan = float(loan_amount)
        except:
            return self.not_applicable_result(rule)
        negs = []
        for v in [cash_from, cash_to]:
            try:
                if v is None:
                    continue
                val = float(v)
                if val < 0:
                    negs.append(abs(val))
            except:
                continue
        if not negs:
            return self.not_applicable_result(rule, details=details)
        max_allowed = min(rule.get('params', {}).get('absolute_limit', 2000),
                          loan * rule.get('params', {}).get('percent_limit', 0.01))
        for amt in negs:
            if amt > max_allowed:
                return self.alert_result(rule, details=details)
        return self.pass_result(rule, details=details)

class HomebuyerProgramValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        cert = resolver.resolve(context, 'los', 'homebuyer_education_certificate')
        details = {'homebuyer_education_certificate': cert}
        if cert and str(cert).strip().lower() in ['yes', 'y', 'true']:
            return self.pass_result(rule, details=details)
        return self.condition_result(rule, message=rule.get('condition_message'), details=details)

class HomebuyerLTVValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        ltv = resolver.resolve(context, 'los', 'ltv')
        cert = resolver.resolve(context, 'los', 'homebuyer_education_certificate')
        details = {'ltv': ltv, 'homebuyer_education_certificate': cert}
        try:
            lp = float(ltv)
            if lp <= 1:
                lp = lp * 100   
        except:
            return self.not_applicable_result(rule)
        if lp > rule.get('params', {}).get('max_ltv', 95):
            if cert and str(cert).strip().lower() in ['yes', 'y', 'true']:
                return self.pass_result(rule, details=details)
            return self.condition_result(rule, message=rule.get('condition_message'), details=details)
        return self.pass_result(rule, details=details)

class IncomeValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        income = resolver.resolve(context, 'los', 'total_income')
        ami = resolver.resolve(context, 'los', 'area_median_income') or rule.get('params', {}).get('area_median_income')
        details = {'total_income': income, 'area_median_income': ami}
        try:
            inc = float(income)
            ami_v = float(ami)
        except:
            return self.not_applicable_result(rule)
        if inc > ami_v:
            return self.alert_result(rule, details=details)
        return self.pass_result(rule, details=details)

class LienPayoffValidator(BaseValidator):
    def evaluate(self, rule, context, resolver):
        paid_off = resolver.resolve(context, 'los', 'liabilities_will_be_paid_off')
        acc_type = resolver.resolve(context, 'los', 'liabilities_account_type')
        liab_names = resolver.resolve(context, 'los', 'liabilities_name') or ""
        details = {'paid_off': paid_off, 'account_type': acc_type, 'liabilities_name': liab_names}
        if str(paid_off).strip().lower() in ['yes', 'true']:
            count = 0
            if isinstance(liab_names, str) and liab_names.strip():
                count = len([x for x in liab_names.split(',') if x.strip()])
            if count > 1:
                return self.alert_result(rule, details=details)
            if not acc_type or 'mortgage' not in str(acc_type).lower():
                return self.alert_result(rule, details=details)
        return self.pass_result(rule, details=details)

# -------------------------------------------------
# RULE DISPATCHER
# -------------------------------------------------
class RuleDispatcher:
    def __init__(self):
        self.resolver = PathResolver()
        self.rules = self._load_rules()
        self.validators = {
            "LTVValidator": LTVValidator(),
            "DTIValidator": DTIValidator(),
            "OccupancyValidator": OccupancyValidator(),
            "SecondHomeValidator": SecondHomeValidator(),
            "InvestmentValidator": InvestmentValidator(),
            "CreditScoreValidator": CreditScoreValidator(),
            "GiftValidator": GiftValidator(),
            "CashoutSeasoningValidator": CashoutSeasoningValidator(),
            "TitleValidator": TitleValidator(),
            "FraudValidator": FraudValidator(),
            "AppraisalPriorSaleValidator": AppraisalPriorSaleValidator(),
            "LoanProgramValidator": LoanProgramValidator(),
            "CashbackValidator": CashbackValidator(),
            "HomebuyerProgramValidator": HomebuyerProgramValidator(),
            "HomebuyerLTVValidator": HomebuyerLTVValidator(),
            "IncomeValidator": IncomeValidator(),
            "LienPayoffValidator": LienPayoffValidator(),
        }

    def _load_rules(self):
        with open(PPV_CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)["rules"]

    # ---------- TRIGGER ENGINE ----------
    def _norm(self, v):
        return str(v).strip().lower() if v is not None else ""

    def _match_value(self, actual, expected):
        if isinstance(expected, str) and expected.startswith("GT"):
            try:
                v = float(actual)
                if v <= 1:
                    v *= 100
                return v > float(expected[2:])
            except:
                return False
        return self._norm(actual) == self._norm(expected)

    def _match_trigger(self, trigger, context):
        for key, expected_list in trigger.items():
            if key == "or":
                return any(self._match_trigger(x, context) for x in expected_list)

            actual = self.resolver.resolve(context, "los", key)
            if not any(self._match_value(actual, exp) for exp in expected_list):
                return False
        return True

    def evaluate(self, context):
        results = {}
        for rule in self.rules:
            validator = self.validators.get(rule["validator"])
            if not validator:
                continue

            if not self._match_trigger(rule.get("trigger", {}), context):
                results[rule["id"]] = validator.not_applicable_result(rule)
                continue

            results[rule["id"]] = validator.evaluate(rule, context, self.resolver)
        return results


# -------------------------------------------------
# MAIN LPP VALIDATOR
# -------------------------------------------------
class LPPValidator:
    def __init__(self):
        self.mongo = MongoClientWrapper()
        self.dispatcher = RuleDispatcher()

    def format_results(self, los_data, validation_results):
        resolver = self.dispatcher.resolver
        ctx = {"los": los_data}

        result = {
            "loan_id": resolver.resolve(ctx, "los", "loan_id"),
            "borrower": {
                "names": [
                    resolver.resolve(ctx, "los", "borrower_first_name"),
                    resolver.resolve(ctx, "los", "borrower_middle_name"),
                    resolver.resolve(ctx, "los", "borrower_last_name"),
                ]
            },
            "loan_details": {
                "application_date": resolver.resolve(ctx, "los", "application_date"),
                "program": resolver.resolve(ctx, "los", "loan_program"),
                "closing_date": resolver.resolve(ctx, "los", "closing_date"),
                "purchase_price": float(resolver.resolve(ctx, "los", "purchase_price") or 0),
                "loan_amount": float(resolver.resolve(ctx, "los", "loan_amount") or 0),
                "le_due_date": resolver.resolve(ctx, "los", "le_due_date"),
                "action_summary": {
                    "total_checks": 0,
                    "alerts_count": 0,
                    "updates_count": 0,
                    "conditions_count": 0
                }
            },
            "alerts": [],
            "los_updates": [],
            "conditions": []
        }

        # populate alerts / conditions
        for rule in validation_results.values():
            status = rule.get("status")
            message = rule.get("message")

            if status == "ALERT":
                result["alerts"].append({"message": message})
                result["loan_details"]["action_summary"]["alerts_count"] += 1

            elif status == "CONDITION":
                result["conditions"].append({"message": message})
                result["loan_details"]["action_summary"]["conditions_count"] += 1

        s = result["loan_details"]["action_summary"]
        s["total_checks"] = (
            s["alerts_count"] +
            s["updates_count"] +
            s["conditions_count"]
        )

        return result

    def validate_loan(self, loan_id: str):
        # 1️⃣ Load LOS
        los = self.mongo.get_los_by_loanid(loan_id)
        if not los:
            raise Exception("LOS not found")

        context = {
            "los": los,
            "credit_report": {},
            "title": {},
            "appraisal": {},
            "drive_report": {}
        }

        # 2️⃣ Run all rules
        validation_results = self.dispatcher.evaluate(context)

        # 3️⃣ Build FINAL UI OBJECT
        final_result = self.format_results(los, validation_results)

        # 4️⃣ Save EXACT SAME OBJECT to DB
        result_id = self.mongo.save_disclosure_ppv(final_result)

        # 5️⃣ Return EXACT SAME OBJECT to caller
        return final_result
# -------------------------------------------------
# CLI ENTRY
# -------------------------------------------------
def main():
    import argparse, json

    parser = argparse.ArgumentParser(description="Program & Product Validator")
    parser.add_argument("loan_id")
    args = parser.parse_args()

    validator = LPPValidator()
    result = validator.validate_loan(args.loan_id)
    print(json.dumps(result, indent=2, default=str))

if __name__ == "__main__":
    main()

