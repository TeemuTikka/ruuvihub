function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : "";
}

(function () {
  const button = document.getElementById("test-upload-button");
  const result = document.getElementById("test-upload-result");
  const destination = document.getElementById("cloud_upload_destination");

  if (!button || !result) return;

  function updateDestination() {
    document.querySelectorAll("[data-upload-destination]").forEach(field => {
      field.hidden = field.dataset.uploadDestination !== destination.value;
      field.querySelectorAll("input").forEach(input => {
        input.disabled = field.hidden;
      });
    });
    result.textContent = "";
    result.classList.remove("success-msg", "error");
  }
  if (destination) {
    destination.addEventListener("change", updateDestination);
    updateDestination();
  }

  function setResult(message, success) {
    result.textContent = message;
    result.classList.toggle("success-msg", success);
    result.classList.toggle("error", !success);
  }

  button.addEventListener("click", async function (event) {
    event.preventDefault();

    const endpoint = document.getElementById("cloud_endpoint_url");
    const headerName = document.getElementById("cloud_auth_header_name");
    const headerValue = document.getElementById("cloud_auth_header_value");
    const azureContainer = document.getElementById("azure_blob_container_url");

    setResult("Testing connection...", true);
    button.disabled = true;

    try {
      const response = await fetch("/api/config/test-upload", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken() },
        body: new URLSearchParams({
          cloud_endpoint_url: endpoint ? endpoint.value : "",
          cloud_auth_header_name: headerName ? headerName.value : "",
          cloud_auth_header_value: headerValue ? headerValue.value : "",
          cloud_upload_destination: destination ? destination.value : "http",
          azure_blob_container_url: azureContainer ? azureContainer.value : "",
        }),
        credentials: "same-origin",
      });

      let payload = {};
      try {
        payload = await response.json();
      } catch (err) {
        payload = {};
      }

      if (!response.ok) {
        throw new Error(payload.detail || `HTTP ${response.status}`);
      }

      setResult(payload.message || "Connection test finished.", !!payload.success);
    } catch (err) {
      setResult(err.message || "Connection test failed.", false);
    } finally {
      button.disabled = false;
    }
  });
})();
