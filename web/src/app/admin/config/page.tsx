import { redirect } from "next/navigation";

export default function AdminConfigRedirect() {
  redirect("/staff/tools");
}
