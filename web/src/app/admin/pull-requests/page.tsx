import { redirect } from "next/navigation";

export default function AdminPullRequestsRedirect() {
  redirect("/staff/pull-requests");
}
