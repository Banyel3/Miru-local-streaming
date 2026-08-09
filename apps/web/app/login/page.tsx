import { Suspense } from "react";
import { LoginForm } from "./LoginForm";

export const metadata = { title: "Sign in — Miru" };

/** The one page the public gate leaves open. Everything else 401s until the
 *  cookie this flow sets exists. */
export default function LoginPage() {
  return (
    <main className="grid min-h-dvh place-items-center bg-bg-deep p-6">
      <Suspense>
        <LoginForm />
      </Suspense>
    </main>
  );
}
