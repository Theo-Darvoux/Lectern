import { redirect } from "next/navigation";

export default function ModeratorFeaturedRedirect() {
  redirect("/staff/content?tab=featured");
}
