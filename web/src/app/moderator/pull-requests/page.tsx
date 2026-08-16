import { redirect } from "next/navigation";

export default function ModeratorPullRequestsRedirect() {
  redirect("/staff/pull-requests");
}
