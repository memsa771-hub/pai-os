import Avatar from 'boring-avatars';
import { cn } from '@/lib/utils';

const OA_PALETTE = ['#6366F1', '#8B5CF6', '#06B6D4', '#10B981', '#F59E0B'];

// The built-in PAI Counselor assistant has a fixed brand avatar instead of a generated
// one. Its internal agent name is reserved/unique, so matching
// on the name is sufficient to identify it wherever an avatar is rendered.
const PAI_AVATAR_SRC = '/pai-avatar.png';
const isPai = (name: string) => (name || '').toLowerCase() === 'pai';

interface AgentAvatarProps {
  name: string;
  size?: number;
  status?: string;
  showStatus?: boolean;
  className?: string;
  square?: boolean;
}

export function AgentAvatar({ name, size = 28, status, showStatus = false, className, square = false }: AgentAvatarProps) {
  return (
    <div className={cn('relative shrink-0', className)} style={{ width: size, height: size }}>
      <div className={cn(square ? 'rounded-lg' : 'rounded-full', 'overflow-hidden')} style={{ width: size, height: size }}>
        {isPai(name) ? (
          <img
            src={PAI_AVATAR_SRC}
            alt="PAI Counselor"
            width={size}
            height={size}
            className="size-full object-cover"
            draggable={false}
          />
        ) : (
          <Avatar name={name} size={size} variant="beam" colors={OA_PALETTE} square={square} />
        )}
      </div>
      {showStatus && (
        <span className={cn(
          'absolute -bottom-0.5 -right-0.5 rounded-full border-[1.5px] border-background',
          size >= 28 ? 'size-2.5' : 'size-2',
          status === 'online' ? 'bg-green-500' : 'bg-zinc-300 dark:bg-zinc-600'
        )} />
      )}
    </div>
  );
}
