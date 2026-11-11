import { Link } from "react-router";

export function NotFound() {
  return (
    <div className="py-20 text-center text-white/50">
      <p className="text-lg text-white/80">Nothing here.</p>
      <Link to="/" className="focus-ring mt-2 inline-block rounded text-sky-300 hover:underline">
        Back to overview
      </Link>
    </div>
  );
}
