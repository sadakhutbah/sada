import type { Session } from "./api";

/** Session of the signed-in user, readable outside React (permissions checks in pages). */
let sessionCourante: Session | null = null;
export const currentSession = () => sessionCourante;
export const setCurrentSession = (s: Session | null) => { sessionCourante = s; };
