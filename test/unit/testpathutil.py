import os
import unittest
from manta_trading.util.pathutil import PathUtil

class TestPathUtil(unittest.TestCase):

    def setUp(self):
        # Create a temporary directory for testing
        self.temp_dir = os.path.join(os.path.dirname(__file__), 'temp_dir')
        os.makedirs(self.temp_dir, exist_ok=True)

    def tearDown(self):
        for filename in os.listdir(self.temp_dir):
            file_path = os.path.join(self.temp_dir, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    os.rmdir(file_path)
            except Exception as e:
                print(f"Failed to delete {file_path}. Reason: {e}")
        os.rmdir(self.temp_dir)
    def test_generate_merged_output_filename(self):
        # Test case 1: Generate a filename with no existing file
        symbol = 'AAPL'
        date = '2022-01-01'
        output_path = self.temp_dir
        date_format = '%Y%m'
        expected_filename = os.path.join(output_path, 'AAPL-202201.csv')

        result_filename = PathUtil.generateMergedOutputFilename(symbol, date, output_path, date_format)
        self.assertEqual(result_filename, expected_filename)

        # Test case 2: Generate a filename with an existing file
        symbol = 'GOOGL'
        date = '2022-01-01'
        output_path = self.temp_dir
        date_format = '%Y%m'
        expected_filename = os.path.join(output_path, 'GOOGL-202201-1.csv')

        # Create a dummy file to simulate the existing file
        with open(os.path.join(output_path, 'GOOGL-202201.csv'), 'w') as f:
            f.write('dummy content')

        result_filename = PathUtil.generateMergedOutputFilename(symbol, date, output_path, date_format)
        self.assertEqual(result_filename, expected_filename)

        # Test case 3: Generate a filename with an existing file and increment the index
        symbol = 'MSFT'
        date = '2022-01-01'
        output_path = self.temp_dir
        date_format = '%Y-%m-%d'
        expected_filename = os.path.join(output_path, 'MSFT-2022-01-01-1.csv')

        # Create a dummy file to simulate the existing file
        with open(os.path.join(output_path, 'MSFT-2022-01-01.csv'), 'w') as f:
            f.write('dummy content')

        result_filename = PathUtil.generateMergedOutputFilename(symbol, date, output_path, date_format)
        self.assertEqual(result_filename, expected_filename)

