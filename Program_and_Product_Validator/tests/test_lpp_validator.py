"""
Test script for LPP Validator
"""
import os
import sys
import unittest
from datetime import datetime
from pymongo import MongoClient
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.lpp import LPPValidator, MongoClientWrapper

# Load environment variables
load_dotenv()

class TestLPPValidator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Set up test data"""
        cls.test_loan_id = "TEST_LOAN_123"
        cls.mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        cls.db_name = os.getenv("MONGO_DB", "3NP_Mortgage_AI_Test")
        
        # Initialize test client
        cls.client = MongoClient(cls.mongo_uri)
        cls.db = cls.client[cls.db_name]
        
        # Insert test data
        cls.setup_test_data()
    
    @classmethod
    def tearDownClass(cls):
        """Clean up test data"""
        # Clean up test collections
        cls.db.LOS_Data.delete_many({"loan_id": cls.test_loan_id})
        cls.db.Document_Indexer_DEV.delete_many({"loan_id": cls.test_loan_id})
        cls.db.DISCLOSURE_PPV.delete_many({"loan_id": cls.test_loan_id})
        cls.client.close()
    
    @classmethod
    def setup_test_data(cls):
        """Insert test data into MongoDB"""
        # Test LOS data
        los_data = {
            "loan_id": cls.test_loan_id,
            "purpose_of_loan": "Purchase",
            "no_units": 1,
            "property_will_be": "Primary",
            "amortization_type": "Fixed Rate",
            "mortgage_type_applied_for": "Conventional",
            "investor": "Fannie Mae",
            "ltv": 98,  # This should trigger LTV alert
            "cltv": 98,
            "hcltv": 98,
            "dti": 45,
            "gift_amount": 5000,
            "liabilities_account_number": "1234567890",
            "liabilities_name": "Test Creditor",
            "estimated_closing_date": "2025-12-31"
        }
        
        # Test indexer data
        indexer_data = {
            "loan_id": cls.test_loan_id,
            "document_type": "Credit Report",
            "pages": [
                {
                    "page_no": 1,
                    "fields": {
                        "Creditor Account Number": {"value": "1234567890", "confidence": 0.95},
                        "Creditor Name": {"value": "Test Creditor", "confidence": 0.95},
                        "Date_Opened": {"value": "2020-01-01", "confidence": 0.95}
                    }
                }
            ]
        }
        
        # Insert test data
        cls.db.LOS_Data.insert_one(los_data)
        cls.db.Document_Indexer_DEV.insert_one(indexer_data)
    
    def test_validate_loan(self):
        """Test loan validation"""
        # Initialize validator
        validator = LPPValidator(self.mongo_uri, self.db_name)
        
        # Run validation
        result = validator.validate_loan(self.test_loan_id)
        
        # Check if validation was successful
        self.assertEqual(result['status'], 'success')
        self.assertIn('result_id', result)
        self.assertIn('results', result)
        
        # Check if results were saved to DISCLOSURE_PPV
        saved_result = self.db.DISCLOSURE_PPV.find_one({"loan_id": self.test_loan_id})
        self.assertIsNotNone(saved_result)
        
        # Check if alerts were properly categorized
        self.assertIn('alerts', saved_result)
        self.assertGreater(len(saved_result.get('alerts', [])), 0)
        
        # Check action summary
        self.assertIn('action_summary', saved_result.get('loan_details', {}))
        summary = saved_result['loan_details']['action_summary']
        self.assertGreater(summary['total_checks'], 0)
        self.assertGreaterEqual(summary['alerts_count'], 0)
        
        print("\nTest Results:")
        print(f"- Total Checks: {summary['total_checks']}")
        print(f"- Alerts: {summary['alerts_count']}")
        print(f"- Conditions: {summary['conditions_count']}")
        
        # Print first alert if any
        if saved_result.get('alerts'):
            print("\nFirst Alert:", saved_result['alerts'][0]['message'])

if __name__ == "__main__":
    unittest.main()
