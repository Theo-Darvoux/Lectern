from locust import HttpUser, task, between

class LecternUser(HttpUser):
    wait_time = between(1, 5)  # simulate real user think time

    def on_start(self):
        # Initial page load simulating a user opening the site
        self.client.get("/api/auth/methods")
        self.client.get("/api/home")

    @task(3)
    def visit_homepage(self):
        """Simulate a user reloading or returning to the homepage."""
        self.client.get("/api/home", name="/api/home")

    @task(2)
    def browse_root(self):
        """Simulate browsing the root directory."""
        self.client.get("/api/browse", name="/api/browse (root)")

    @task(1)
    def browse_deep_path(self):
        """Simulate browsing a deeper path (assuming some common path structure).
        Adjust this path based on your actual data structure."""
        # Using a dummy deep path here. In reality, it will hit the DB
        # and likely return a 404 if not found, but it tests the CTE performance
        # for resolution.
        self.client.get("/api/browse?path=/l3/droit-ue", name="/api/browse?path=/l3/droit-ue")

    @task(1)
    def check_auth(self):
        """Simulate checking auth status."""
        self.client.get("/api/auth/methods", name="/api/auth/methods")
