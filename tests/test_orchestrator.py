import unittest
from unittest.mock import patch, MagicMock, mock_open
import json
import os
import tempfile
import sys

import orchestrator
from orchestrator import load_queue, save_queue, run_orchestrator

class TestOrchestrator(unittest.TestCase):

    def setUp(self):
        # Create a temporary file for the queue to isolate test runs
        self.temp_queue_fd, self.temp_queue_path = tempfile.mkstemp()
        os.close(self.temp_queue_fd)
        
        self.original_queue_file = orchestrator.QUEUE_FILE
        orchestrator.QUEUE_FILE = self.temp_queue_path
        
        # Suppress printing to stdout and stderr to keep unit test runs clean
        self.stdout_patcher = patch('sys.stdout', new_callable=MagicMock)
        self.stderr_patcher = patch('sys.stderr', new_callable=MagicMock)
        self.mock_stdout = self.stdout_patcher.start()
        self.mock_stderr = self.stderr_patcher.start()

    def tearDown(self):
        # Restore original queue file configuration and clean up
        orchestrator.QUEUE_FILE = self.original_queue_file
        if os.path.exists(self.temp_queue_path):
            os.remove(self.temp_queue_path)
            
        self.stdout_patcher.stop()
        self.stderr_patcher.stop()

    def test_load_queue_missing_file(self):
        # Delete temporary queue file to simulate missing file
        if os.path.exists(self.temp_queue_path):
            os.remove(self.temp_queue_path)
            
        queue = load_queue()
        self.assertEqual(queue, {"processed_urls": [], "pending_articles": []})

    def test_load_queue_corrupted_file(self):
        # Write corrupted JSON to the queue file
        with open(self.temp_queue_path, "w") as f:
            f.write("{invalid json")
            
        # Should catch JSONDecodeError and return default structure
        queue = load_queue()
        self.assertEqual(queue, {"processed_urls": [], "pending_articles": []})

    @patch("builtins.open", side_effect=PermissionError("Permission Denied"))
    def test_load_queue_permission_error(self, mock_open_err):
        # Should catch PermissionError and return default structure
        queue = load_queue()
        self.assertEqual(queue, {"processed_urls": [], "pending_articles": []})

    def test_save_queue_success(self):
        test_queue = {
            "processed_urls": ["https://example.com/1"],
            "pending_articles": [{"url": "https://example.com/2", "title": "Test"}]
        }
        save_queue(test_queue)
        
        # Verify it saved correctly by loading and comparing
        loaded = load_queue()
        self.assertEqual(loaded, test_queue)

    @patch("builtins.open", side_effect=PermissionError("Permission Denied"))
    def test_save_queue_failure(self, mock_open_err):
        test_queue = {"processed_urls": [], "pending_articles": []}
        # In save_queue, open failure should propagate (no internal try-except block)
        with self.assertRaises(PermissionError):
            save_queue(test_queue)

    @patch("orchestrator.run_aggregation")
    @patch("orchestrator.generate_events")
    @patch("time.sleep", side_effect=KeyboardInterrupt("Loop Break"))
    def test_orchestrator_push_ignores_duplicates(self, mock_sleep, mock_gen_events, mock_agg):
        # Setup initial queue status
        initial_queue = {
            "processed_urls": ["http://already-processed.com"],
            "pending_articles": [{"url": "http://already-pending.com", "title": "Old news"}]
        }
        save_queue(initial_queue)
        
        # Mock run_aggregation to return some articles
        # One processed duplicate, one pending duplicate, one new article, one with missing url
        mock_agg.return_value = {
            "categories": {
                "Politics": [
                    {"url": "http://already-processed.com", "title": "Processed Duplicate"},
                    {"url": "http://already-pending.com", "title": "Pending Duplicate"},
                    {"url": "http://new-news.com", "title": "New Unique News"},
                    {"url": "", "title": "Missing URL"}
                ]
            }
        }
        
        mock_gen_events.return_value = False # Keep queue uncleared for verification
        
        try:
            run_orchestrator(sleep_interval=1)
        except KeyboardInterrupt:
            pass # Expected loop break via time.sleep mock
            
        # Verify the saved queue
        final_queue = load_queue()
        self.assertEqual(final_queue["processed_urls"], ["http://already-processed.com"])
        
        # Should contain "already-pending" and "new-news"
        pending_urls = [a["url"] for a in final_queue["pending_articles"]]
        self.assertIn("http://already-pending.com", pending_urls)
        self.assertIn("http://new-news.com", pending_urls)
        self.assertNotIn("http://already-processed.com", pending_urls)
        self.assertNotIn("", pending_urls)
        self.assertEqual(len(final_queue["pending_articles"]), 2)

    @patch("orchestrator.run_aggregation")
    @patch("orchestrator.generate_events")
    @patch("time.sleep", side_effect=KeyboardInterrupt("Loop Break"))
    def test_orchestrator_threshold_met_success(self, mock_sleep, mock_gen_events, mock_agg):
        # 5 pending articles met the threshold
        initial_queue = {
            "processed_urls": [],
            "pending_articles": [{"url": f"http://news{i}.com", "title": f"Title {i}"} for i in range(5)]
        }
        save_queue(initial_queue)
        
        # No new articles from scraping
        mock_agg.return_value = {"categories": {}}
        # Event generation succeeded
        mock_gen_events.return_value = True
        
        try:
            run_orchestrator(sleep_interval=1)
        except KeyboardInterrupt:
            pass
            
        final_queue = load_queue()
        
        # generate_events should be called with the pending articles
        mock_gen_events.assert_called_once()
        called_args, called_kwargs = mock_gen_events.call_args
        self.assertEqual(len(called_kwargs["articles"]), 5)
        
        # Since generation succeeded, pending should be cleared and moved to processed
        self.assertEqual(final_queue["pending_articles"], [])
        self.assertEqual(len(final_queue["processed_urls"]), 5)
        self.assertEqual(set(final_queue["processed_urls"]), {f"http://news{i}.com" for i in range(5)})

    @patch("orchestrator.run_aggregation")
    @patch("orchestrator.generate_events")
    @patch("time.sleep", side_effect=KeyboardInterrupt("Loop Break"))
    def test_orchestrator_threshold_met_failure(self, mock_sleep, mock_gen_events, mock_agg):
        # 5 pending articles met the threshold
        initial_queue = {
            "processed_urls": [],
            "pending_articles": [{"url": f"http://news{i}.com", "title": f"Title {i}"} for i in range(5)]
        }
        save_queue(initial_queue)
        
        # No new articles
        mock_agg.return_value = {"categories": {}}
        # Event generation failed (returns False)
        mock_gen_events.return_value = False
        
        try:
            run_orchestrator(sleep_interval=1)
        except KeyboardInterrupt:
            pass
            
        final_queue = load_queue()
        
        # generate_events should be called
        mock_gen_events.assert_called_once()
        
        # Since generation failed, pending should NOT be cleared or marked as processed
        self.assertEqual(len(final_queue["pending_articles"]), 5)
        self.assertEqual(final_queue["processed_urls"], [])

    @patch("orchestrator.run_aggregation")
    @patch("orchestrator.generate_events")
    @patch("time.sleep", side_effect=KeyboardInterrupt("Loop Break"))
    def test_orchestrator_threshold_not_met(self, mock_sleep, mock_gen_events, mock_agg):
        # Only 4 pending articles (threshold is 5)
        initial_queue = {
            "processed_urls": [],
            "pending_articles": [{"url": f"http://news{i}.com", "title": f"Title {i}"} for i in range(4)]
        }
        save_queue(initial_queue)
        
        mock_agg.return_value = {"categories": {}}
        
        try:
            run_orchestrator(sleep_interval=1)
        except KeyboardInterrupt:
            pass
            
        final_queue = load_queue()
        
        # generate_events should NOT be called
        mock_gen_events.assert_not_called()
        
        # Pending queue remains unchanged
        self.assertEqual(len(final_queue["pending_articles"]), 4)
        self.assertEqual(final_queue["processed_urls"], [])

    @patch("orchestrator.logger")
    @patch("orchestrator.run_aggregation", side_effect=RuntimeError("Aggregation failure"))
    @patch("time.sleep", side_effect=KeyboardInterrupt("Loop Break"))
    def test_orchestrator_run_aggregation_exception(self, mock_sleep, mock_agg, mock_logger):
        # Exception during run_aggregation should be caught inside the loop
        # and should not prevent execution of subsequent steps (like sleep)
        try:
            run_orchestrator(sleep_interval=1)
        except KeyboardInterrupt:
            pass
            
        # Verify aggregation was called
        mock_agg.assert_called_once()
        # Verify the exception message was logged
        mock_logger.error.assert_any_call("Error in orchestrator loop: Aggregation failure", exc_info=True)

    @patch("orchestrator.run_aggregation")
    @patch("orchestrator.generate_events")
    @patch("time.sleep", side_effect=KeyboardInterrupt("Loop Break"))
    def test_orchestrator_news_a_duplicate_injection(self, mock_sleep, mock_gen_events, mock_agg):
        # Write flow messages directly to actual terminal output
        sys.__stdout__.write("\n[Flow Test] Starting duplicate injection test for News A...\n")
        
        # 1. Initialize queue with News A in pending_articles
        news_a_url = "http://news-a.com"
        news_a_article = {"url": news_a_url, "title": "Original News A", "source": "Source A"}
        initial_queue = {
            "processed_urls": [],
            "pending_articles": [news_a_article]
        }
        save_queue(initial_queue)
        sys.__stdout__.write(f"[Flow Test] Step 1: Initialized queue with News A in pending_articles (URL: {news_a_url})\n")
        
        # 2. Mock scraper output containing a duplicate News A (same URL, different title/source)
        duplicate_news_a = {"url": news_a_url, "title": "Duplicate News A - New Title", "source": "Source B"}
        mock_agg.return_value = {
            "categories": {
                "Politics": [duplicate_news_a]
            }
        }
        sys.__stdout__.write(f"[Flow Test] Step 2: Scraper returned a duplicate article (URL: {news_a_url}, Title: '{duplicate_news_a['title']}')\n")
        
        # 3. Run orchestrator loop iteration
        sys.__stdout__.write("[Flow Test] Step 3: Running orchestrator loop...\n")
        mock_gen_events.return_value = False # Make sure it doesn't clear the queue
        
        try:
            run_orchestrator(sleep_interval=1)
        except KeyboardInterrupt:
            pass
            
        # 4. Verify queue status
        final_queue = load_queue()
        pending_list = final_queue["pending_articles"]
        
        sys.__stdout__.write(f"[Flow Test] Step 4: Loading final queue. Pending count: {len(pending_list)}\n")
        
        # Find all occurrences of URL news-a.com in pending articles
        occurrences = [a for a in pending_list if a.get("url") == news_a_url]
        sys.__stdout__.write(f"[Flow Test] Step 5: Found {len(occurrences)} occurrence(s) of URL '{news_a_url}' in pending articles.\n")
        
        # Test assertion: Should have exactly 1 occurrence (the original one)
        self.assertEqual(
            len(occurrences), 1, 
            f"Duplicate article was injected! Expected 1, found {len(occurrences)}"
        )
        # Check that it kept the original News A, and did not overwrite or append duplicate
        self.assertEqual(
            occurrences[0]["title"], "Original News A",
            "The original News A was overwritten by the duplicate!"
        )
        sys.__stdout__.write("[Flow Test] Success: Duplicate news was successfully blocked from entering the queue.\n\n")

if __name__ == "__main__":
    unittest.main()
