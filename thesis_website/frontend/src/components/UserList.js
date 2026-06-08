import React from 'react';

function UserList({ users, onUserDeleted }) {
  const handleDelete = async (id) => {
    try {
      const response = await fetch(`${window.location.protocol}//${window.location.hostname}:5000/api/users/${id}`, {
        method: 'DELETE',
      });
      
      if (response.ok) {
        onUserDeleted();
      }
    } catch (error) {
      console.error('Error deleting user:', error);
    }
  };

  return (
    <div style={styles.container}>
      <h2>User List</h2>
      {users.length === 0 ? (
        <p>No users yet. Add one above!</p>
      ) : (
        <ul style={styles.list}>
          {users.map((user) => (
            <li key={user._id} style={styles.listItem}>
              <div>
                <strong>{user.name}</strong>
                <br />
                <span style={styles.email}>{user.email}</span>
              </div>
              <button
                onClick={() => handleDelete(user._id)}
                style={styles.deleteButton}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const styles = {
  container: {
    maxWidth: '600px',
    margin: '20px auto',
    padding: '20px',
  },
  list: {
    listStyle: 'none',
    padding: 0,
  },
  listItem: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '15px',
    marginBottom: '10px',
    border: '1px solid #ddd',
    borderRadius: '8px',
    backgroundColor: '#f9f9f9',
  },
  email: {
    color: '#666',
    fontSize: '14px',
  },
  deleteButton: {
    padding: '8px 16px',
    backgroundColor: '#dc3545',
    color: 'white',
    border: 'none',
    borderRadius: '4px',
    cursor: 'pointer',
  },
};

export default UserList;
