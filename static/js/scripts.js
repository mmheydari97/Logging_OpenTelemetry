async function loadLogs() {
    const response = await fetch('/api/logs'); 
    const logs = await response.json(); 
    const tbody = document.getElementById('logsTableBody');
    if (logs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="loading">No logs found</td></tr>'; 
        return;
    }
    tbody.innerHTML = logs.map(log => `
        <tr onclick="showLogDetails('${log.id}')">
            <td>${new Date(log.timestamp).toLocaleString()}</td>
            <td>${log.level}</td>
            <td>${log.function_name}</td>
            <td>${log.duration_ms}ms</td>
            <td>${log.status}</td>
            <td>${log.message}</td>
        </tr>
    `).join('');
}

async function showLogDetails(logId) {
    const response = await fetch(`/api/logs/${logId}`); 
    const log = await response.json(); 
    document.getElementById('logDetails').textContent = JSON.stringify(log.raw_data, null, 2);
    document.getElementById('logModal').style.display = 'block'; 
}

function closeModal() { 
    document.getElementById('logModal').style.display = 'none'; 
}

window.onclick = function(event) { 
    if (event.target == document.getElementById('logModal')) { closeModal(); } 
};

document.addEventListener('DOMContentLoaded', () => {
    loadLogs();
    setInterval(loadLogs, 5000);
});
