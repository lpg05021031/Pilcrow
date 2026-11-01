// The to-do list: a TodoWrite step's body, and the Tasks tab's list.
import { Check } from 'lucide-react';


export interface Todo {
  content?: string;
  status?: string;
  activeForm?: string;
}

export function TodoBody({ todos }: { todos: Todo[] }) {
  if (!Array.isArray(todos)) return null;
  return (
    <ul className="todos">
      {todos.map((t, k) => (
        <li key={k} className={`todo ${t.status}`}>
          <span className="todo-box">{t.status === 'completed' ? <Check size={11} /> : t.status === 'in_progress' ? <span className="todo-dot" /> : null}</span>
          <span>{t.status === 'in_progress' ? t.activeForm || t.content : t.content}</span>
        </li>
      ))}
    </ul>
  );
}
